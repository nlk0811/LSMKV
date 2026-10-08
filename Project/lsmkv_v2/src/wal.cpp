#include "lsmkv/wal.h"
#include <cassert>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __APPLE__
#include <fcntl.h>
#ifndef F_FULLFSYNC
#define F_FULLFSYNC 51
#endif
#endif

namespace lsmkv {

// ── CRC32 (same polynomial as zlib / v1) ─────────────────────────────────────
static uint32_t CRC32(const uint8_t* buf, size_t n) {
    uint32_t crc = 0xFFFFFFFF;
    while (n--) {
        crc ^= *buf++;
        for (int k = 0; k < 8; ++k)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1u));
    }
    return ~crc;
}

// ── Little-endian encode/decode ───────────────────────────────────────────────
static void PutU32(uint32_t v, char* p) {
    for (int i=0;i<4;i++) p[i]=(v>>(8*i))&0xFF;
}
static uint32_t GetU32(const char* p) {
    uint32_t v=0;
    for(int i=0;i<4;i++) v|=uint32_t(uint8_t(p[i]))<<(8*i);
    return v;
}

// Header per record: op(1) + key_len(4) + val_len(4) = 9 bytes
static constexpr size_t kHdrSize = 9;
static constexpr size_t kCrcSize = 4;

// ── Durable fsync ─────────────────────────────────────────────────────────────
static Status DurableSync(int fd) {
#ifdef __APPLE__
    if (fcntl(fd, F_FULLFSYNC) == 0) return Status::OK();
    // fallthrough to fsync on error
#endif
    if (fsync(fd) == 0) return Status::OK();
    return Status::IOError("fsync failed");
}

// ── Record encoding ───────────────────────────────────────────────────────────
void WAL::EncodeRecord(OpType op, const Slice& k, const Slice& v,
                        std::string& out) {
    size_t total = kHdrSize + k.size() + v.size() + kCrcSize;
    out.resize(total);
    char* p = out.data();
    p[0] = static_cast<uint8_t>(op);
    PutU32(static_cast<uint32_t>(k.size()), p+1);
    PutU32(static_cast<uint32_t>(v.size()), p+5);
    memcpy(p+9,           k.data(), k.size());
    memcpy(p+9+k.size(),  v.data(), v.size());
    uint32_t crc = CRC32(reinterpret_cast<const uint8_t*>(p), total - kCrcSize);
    PutU32(crc, p + total - kCrcSize);
}

// ── WAL constructor / destructor ──────────────────────────────────────────────

WAL::WAL(const std::string& path, const Options& opts)
    : path_(path), opts_(opts) {
    fd_ = open(path.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
    assert(fd_ >= 0);

    if (opts_.sync_writes) {
        // Start group-commit writer thread
        writer_ = std::thread(&WAL::WriterLoop, this);
    }
}

WAL::~WAL() {
    Close();
}

void WAL::Close() {
    {
        std::lock_guard<std::mutex> lk(mu_);
        shutdown_ = true;
    }
    cv_submit_.notify_all();
    if (writer_.joinable()) writer_.join();
    if (fd_ >= 0) { close(fd_); fd_ = -1; }
}

// ── Group-commit writer loop ──────────────────────────────────────────────────
// KEY v2 IMPROVEMENT:
//   Batches up to opts_.group_commit_max records or waits opts_.group_commit_us
//   microseconds, then issues ONE fsync for the entire batch.
//   Result: ~batch_size× fewer fsyncs → ~batch_size× higher throughput.

void WAL::WriterLoop() {
    while (true) {
        std::vector<PendingWrite*> batch;
        {
            auto deadline = std::chrono::steady_clock::now() +
                            std::chrono::microseconds(opts_.group_commit_us);
            std::unique_lock<std::mutex> lk(mu_);
            cv_submit_.wait_until(lk, deadline, [&]{
                return !queue_.empty() || shutdown_;
            });
            if (shutdown_ && queue_.empty()) break;
            while (!queue_.empty() &&
                   batch.size() < static_cast<size_t>(opts_.group_commit_max)) {
                batch.push_back(queue_.front());
                queue_.pop_front();
            }
        }
        if (batch.empty()) continue;

        // Write all records to the file buffer
        std::string encoded;
        for (auto* pw : batch) {
            EncodeRecord(pw->op, pw->key, pw->value, encoded);
            ssize_t w = write(fd_, encoded.data(),
                              static_cast<ssize_t>(encoded.size()));
            if (w < 0) {
                for (auto* p2 : batch)
                    p2->result.set_value(Status::IOError("write failed"));
                return;
            }
        }

        // ONE fsync for the whole batch — this is the key improvement
        Status s = DurableSync(fd_);
        for (auto* pw : batch) pw->result.set_value(s);
    }
}

// ── Submit ────────────────────────────────────────────────────────────────────

Status WAL::SubmitAndWait(OpType op, const Slice& key, const Slice& value) {
    if (!opts_.sync_writes) {
        // Async path: write directly, no fsync (bench mode)
        std::string encoded;
        EncodeRecord(op, key, value, encoded);
        ssize_t w = write(fd_, encoded.data(),
                          static_cast<ssize_t>(encoded.size()));
        return (w < 0) ? Status::IOError("write failed") : Status::OK();
    }

    // Sync path: submit to group-commit queue
    PendingWrite pw;
    pw.op    = op;
    pw.key   = key.ToString();
    pw.value = value.ToString();
    auto future = pw.result.get_future();
    {
        std::lock_guard<std::mutex> lk(mu_);
        queue_.push_back(&pw);
    }
    cv_submit_.notify_one();
    return future.get();
}

Status WAL::LogPut(const Slice& key, const Slice& value) {
    return SubmitAndWait(OpType::PUT, key, value);
}

Status WAL::LogDelete(const Slice& key) {
    return SubmitAndWait(OpType::DELETE, key, Slice("", 0));
}

// ── Replay ────────────────────────────────────────────────────────────────────

Status WAL::Replay(std::function<void(const WALRecord&)> fn) const {
    int rfd = open(path_.c_str(), O_RDONLY);
    if (rfd < 0) return Status::OK();  // no WAL = fresh DB

    char hdr[kHdrSize];
    WALRecord rec;
    while (true) {
        ssize_t r = read(rfd, hdr, kHdrSize);
        if (r == 0) break;
        if (r < static_cast<ssize_t>(kHdrSize)) break;  // truncated header

        rec.op = static_cast<OpType>(static_cast<uint8_t>(hdr[0]));
        uint32_t klen = GetU32(hdr+1);
        uint32_t vlen = GetU32(hdr+5);

        rec.key.resize(klen);
        rec.value.resize(vlen);

        if (klen > 0 && read(rfd, rec.key.data(), klen) != static_cast<ssize_t>(klen)) break;
        if (vlen > 0 && read(rfd, rec.value.data(), vlen) != static_cast<ssize_t>(vlen)) break;

        char crc_buf[4];
        if (read(rfd, crc_buf, 4) != 4) break;
        uint32_t stored = GetU32(crc_buf);

        // Recompute CRC
        std::string tmp(kHdrSize + klen + vlen, '\0');
        memcpy(tmp.data(), hdr, kHdrSize);
        memcpy(tmp.data() + kHdrSize, rec.key.data(), klen);
        memcpy(tmp.data() + kHdrSize + klen, rec.value.data(), vlen);
        uint32_t actual = CRC32(reinterpret_cast<const uint8_t*>(tmp.data()), tmp.size());
        if (stored != actual) break;  // corruption — stop

        fn(rec);
    }
    close(rfd);
    return Status::OK();
}

// ── Truncate ──────────────────────────────────────────────────────────────────

Status WAL::Truncate() {
    if (fd_ >= 0) { close(fd_); fd_ = -1; }
    int tfd = open(path_.c_str(), O_WRONLY | O_TRUNC | O_CREAT, 0644);
    if (tfd < 0) return Status::IOError("truncate open failed");
    DurableSync(tfd);
    close(tfd);
    fd_ = open(path_.c_str(), O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (fd_ < 0) return Status::IOError("reopen after truncate failed");
    return Status::OK();
}

size_t WAL::SizeBytes() const {
    struct stat st;
    if (stat(path_.c_str(), &st) != 0) return 0;
    return static_cast<size_t>(st.st_size);
}

}  // namespace lsmkv

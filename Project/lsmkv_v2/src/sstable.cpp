#include "lsmkv/sstable.h"
#include "lsmkv/block_cache.h"
#include <cassert>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

#ifdef __APPLE__
#ifndef F_FULLFSYNC
#define F_FULLFSYNC 51
#endif
#endif

namespace lsmkv {

static constexpr uint64_t kMagic    = 0x214b4d56324c534dULL; // "LSMKV2_!"
static constexpr size_t   kFooter   = 32;   // index_off(8)+bloom_off(8)+idx_sz(4)+bloom_sz(4)+magic(8)
static constexpr size_t   kEntryHdr = 9;    // key_len(4)+val_len(4)+flags(1)

static void PutU32LE(uint32_t v, char* p){for(int i=0;i<4;i++)p[i]=(v>>(8*i))&0xFF;}
static void PutU64LE(uint64_t v, char* p){for(int i=0;i<8;i++)p[i]=(v>>(8*i))&0xFF;}
static uint32_t GetU32LE(const char* p){uint32_t v=0;for(int i=0;i<4;i++)v|=uint32_t(uint8_t(p[i]))<<(8*i);return v;}
static uint64_t GetU64LE(const char* p){uint64_t v=0;for(int i=0;i<8;i++)v|=uint64_t(uint8_t(p[i]))<<(8*i);return v;}

static Status DurableSync(int fd) {
#ifdef __APPLE__
    if (fcntl(fd, F_FULLFSYNC) == 0) return Status::OK();
#endif
    return (fsync(fd) == 0) ? Status::OK() : Status::IOError("fsync");
}

// ── Writer ─────────────────────────────────────────────────────────────────────

SSTableWriter::SSTableWriter(const std::string& path, const Options& opts)
    : path_(path), opts_(opts),
      bloom_(BloomFilter(10000, opts.bloom_fpr)) {
    fd_ = open(path.c_str(), O_CREAT | O_WRONLY | O_TRUNC, 0644);
    assert(fd_ >= 0);
}

SSTableWriter::~SSTableWriter() {
    if (fd_ >= 0) close(fd_);
}

void SSTableWriter::FlushBlock() {
    if (buf_.empty()) return;
    index_.push_back({first_key_in_block_,
                      cur_offset_,
                      static_cast<uint32_t>(buf_.size())});
    ssize_t w = write(fd_, buf_.data(), buf_.size());
    (void)w;
    cur_offset_ += buf_.size();
    buf_.clear();
    first_key_in_block_.clear();
}

Status SSTableWriter::Add(const Slice& key, const Slice& value,
                           bool tombstone) {
    if (first_key_in_block_.empty()) first_key_in_block_ = key.ToString();

    uint32_t klen = static_cast<uint32_t>(key.size());
    uint32_t vlen = tombstone ? 0 : static_cast<uint32_t>(value.size());
    uint8_t  flags = tombstone ? 0x01 : 0x00;

    char hdr[kEntryHdr];
    PutU32LE(klen, hdr);
    PutU32LE(vlen, hdr + 4);
    hdr[8] = static_cast<char>(flags);

    buf_.append(hdr, kEntryHdr);
    buf_.append(key.data(), klen);
    if (!tombstone) buf_.append(value.data(), vlen);

    bloom_.Add(key);

    if (buf_.size() >= opts_.block_size) FlushBlock();
    return Status::OK();
}

Status SSTableWriter::Finish() {
    FlushBlock();
    if (fd_ < 0) return Status::IOError("already finished");

    // ── Index block ─────────────────────────────────────────────────────────
    uint64_t index_offset = cur_offset_;
    std::string idx;
    uint32_t n = static_cast<uint32_t>(index_.size());
    idx.resize(4); PutU32LE(n, idx.data());
    for (auto& e : index_) {
        uint32_t kl = static_cast<uint32_t>(e.first_key.size());
        char tmp[16];
        PutU32LE(kl, tmp);
        PutU64LE(e.offset, tmp+4);
        PutU32LE(e.size,   tmp+12);
        idx.append(tmp, 4);
        idx.append(e.first_key);
        idx.append(tmp+4, 12);
    }
    ssize_t w = write(fd_, idx.data(), idx.size()); (void)w;

    // ── Bloom filter block ──────────────────────────────────────────────────
    uint64_t bloom_offset = index_offset + idx.size();
    std::string bloom_data = bloom_.Serialize();
    w = write(fd_, bloom_data.data(), bloom_data.size()); (void)w;

    // ── Footer ──────────────────────────────────────────────────────────────
    char footer[kFooter] = {};
    PutU64LE(index_offset,                    footer);
    PutU64LE(bloom_offset,                    footer+8);
    PutU32LE(static_cast<uint32_t>(idx.size()),        footer+16);
    PutU32LE(static_cast<uint32_t>(bloom_data.size()), footer+20);
    PutU64LE(kMagic,                          footer+24);
    w = write(fd_, footer, kFooter); (void)w;

    DurableSync(fd_);
    close(fd_); fd_ = -1;
    return Status::OK();
}

size_t SSTableWriter::FileSize() const {
    struct stat st;
    if (stat(path_.c_str(), &st) != 0) return 0;
    return static_cast<size_t>(st.st_size);
}

// ── Reader ─────────────────────────────────────────────────────────────────────

SSTableReader::~SSTableReader() {
    if (fd_ >= 0) close(fd_);
}

std::unique_ptr<SSTableReader> SSTableReader::Open(const std::string& path,
                                                     BlockCache* cache,
                                                     Status* s) {
    auto r = std::unique_ptr<SSTableReader>(new SSTableReader());
    r->path_  = path;
    r->cache_ = cache;
    r->fd_    = open(path.c_str(), O_RDONLY);
    if (r->fd_ < 0) { *s = Status::IOError("open failed: " + path); return nullptr; }
    if (!(*s = r->LoadFooter()).ok())  return nullptr;
    if (!(*s = r->LoadIndex()).ok())   return nullptr;
    if (!(*s = r->LoadBloom()).ok())   return nullptr;
    *s = Status::OK();
    return r;
}

Status SSTableReader::LoadFooter() {
    struct stat st; fstat(fd_, &st);
    if (static_cast<size_t>(st.st_size) < kFooter)
        return Status::Corruption("file too small: " + path_);

    char buf[kFooter];
    pread(fd_, buf, kFooter, st.st_size - kFooter);
    uint64_t magic = GetU64LE(buf + 24);
    if (magic != kMagic)
        return Status::Corruption("bad magic in " + path_);
    index_offset_ = GetU64LE(buf);
    bloom_offset_ = GetU64LE(buf+8);
    index_size_   = GetU32LE(buf+16);
    bloom_size_   = GetU32LE(buf+20);
    return Status::OK();
}

Status SSTableReader::LoadIndex() {
    std::string raw(index_size_, '\0');
    pread(fd_, raw.data(), index_size_, static_cast<off_t>(index_offset_));
    const char* p = raw.data();
    uint32_t n = GetU32LE(p); p += 4;
    for (uint32_t i = 0; i < n; ++i) {
        uint32_t kl = GetU32LE(p); p += 4;
        std::string key(p, kl); p += kl;
        uint64_t off = GetU64LE(p); p += 8;
        uint32_t sz  = GetU32LE(p); p += 4;
        index_.push_back({std::move(key), off, sz});
    }
    return Status::OK();
}

Status SSTableReader::LoadBloom() {
    std::string raw(bloom_size_, '\0');
    pread(fd_, raw.data(), bloom_size_, static_cast<off_t>(bloom_offset_));
    Status s;
    bloom_ = BloomFilter::Deserialize(Slice(raw), &s);
    return s;
}

Status SSTableReader::ReadBlock(uint64_t offset, uint32_t size,
                                 std::string* out) const {
    // Check block cache first
    if (cache_ && cache_->Lookup(path_, offset, out)) return Status::OK();

    out->resize(size);
    ssize_t r = pread(fd_, out->data(), size, static_cast<off_t>(offset));
    if (r != static_cast<ssize_t>(size))
        return Status::IOError("short read in " + path_);

    if (cache_) cache_->Insert(path_, offset, *out);
    return Status::OK();
}

int SSTableReader::BlockIdx(const Slice& key) const {
    // Binary search: last block whose first_key <= key
    int lo = 0, hi = static_cast<int>(index_.size()) - 1, res = 0;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (Slice(index_[mid].first_key) <= key) { res = mid; lo = mid+1; }
        else hi = mid-1;
    }
    return res;
}

bool SSTableReader::MayContain(const Slice& key) const {
    return bloom_.MayContain(key);
}

Status SSTableReader::SearchBlock(const std::string& data, const Slice& key,
                                   std::string* value, bool* tombstone) const {
    const char* p   = data.data();
    const char* end = p + data.size();
    while (p + static_cast<ptrdiff_t>(kEntryHdr) <= end) {
        uint32_t kl    = GetU32LE(p);
        uint32_t vl    = GetU32LE(p+4);
        uint8_t  flags = static_cast<uint8_t>(p[8]);
        p += kEntryHdr;
        if (p + kl + vl > end) break;
        Slice k(p, kl);
        if (k == key) {
            if (tombstone) *tombstone = (flags & 0x01) != 0;
            if (value) value->assign(p + kl, vl);
            return Status::OK();
        }
        p += kl + vl;
    }
    return Status::NotFound("key not in block");
}

Status SSTableReader::Get(const Slice& key, std::string* value,
                           bool* tombstone) const {
    if (!MayContain(key)) return Status::NotFound("bloom");
    if (index_.empty())   return Status::NotFound("empty index");
    int idx = BlockIdx(key);
    std::string block;
    auto s = ReadBlock(index_[idx].offset, index_[idx].size, &block);
    if (!s.ok()) return s;
    return SearchBlock(block, key, value, tombstone);
}

Status SSTableReader::Scan(const Slice& start, const Slice& end,
                            std::function<void(const Slice&,const Slice&,bool)> fn) const {
    int si = start.empty() ? 0 : BlockIdx(start);
    for (int i = si; i < static_cast<int>(index_.size()); ++i) {
        if (!end.empty() && Slice(index_[i].first_key) >= end) break;
        std::string block;
        auto s = ReadBlock(index_[i].offset, index_[i].size, &block);
        if (!s.ok()) return s;
        const char* p = block.data();
        const char* e = p + block.size();
        while (p + static_cast<ptrdiff_t>(kEntryHdr) <= e) {
            uint32_t kl = GetU32LE(p), vl = GetU32LE(p+4);
            bool tomb = (static_cast<uint8_t>(p[8]) & 0x01) != 0;
            p += kEntryHdr;
            if (p + kl + vl > e) break;
            Slice k(p, kl), v(p+kl, vl);
            if (!start.empty() && k < start) { p += kl+vl; continue; }
            if (!end.empty()   && k >= end)  return Status::OK();
            fn(k, v, tomb);
            p += kl + vl;
        }
    }
    return Status::OK();
}

}  // namespace lsmkv

#pragma once
/*
 * v2 KEY IMPROVEMENT: Group-Commit Write-Ahead Log
 * ─────────────────────────────────────────────────
 * v1 finding: per-write fsync costs 790× in throughput on modern SSD.
 *
 * Solution: a background writer thread batches multiple records into one
 * write + fsync.  Each caller submits a PendingWrite and blocks on a
 * future until the batch is fsynced — durability is still guaranteed for
 * every acknowledged write, but the fsync cost is amortised across the batch.
 *
 * With batch_size=128, expected throughput: ~128× v1 sync throughput.
 * Durability contract: identical to per-write fsync (Invariant 1 satisfied).
 *
 * Wire format (per record):
 *   op_type  : 1 byte  (1=PUT, 2=DELETE)
 *   key_len  : 4 bytes (uint32, little-endian)
 *   val_len  : 4 bytes (uint32, little-endian)
 *   key      : key_len bytes
 *   value    : val_len bytes
 *   crc32    : 4 bytes (CRC of all bytes above)
 */
#include "types.h"
#include "status.h"
#include "options.h"
#include <cstdint>
#include <deque>
#include <future>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <vector>

namespace lsmkv {

enum class OpType : uint8_t { PUT = 1, DELETE = 2 };

struct WALRecord {
    OpType      op;
    std::string key;
    std::string value;
};

class WAL {
public:
    explicit WAL(const std::string& path, const Options& opts);
    ~WAL();

    WAL(const WAL&)            = delete;
    WAL& operator=(const WAL&) = delete;

    // Submit a write to the group-commit queue.  Blocks until the record
    // is fsync'd as part of a batch.
    Status LogPut(const Slice& key, const Slice& value);
    Status LogDelete(const Slice& key);

    // Replay all valid records from WAL into the caller-supplied callback.
    // Stops at the first CRC mismatch or truncated record (crash point).
    Status Replay(std::function<void(const WALRecord&)> fn) const;

    // Truncate to zero — call ONLY after MANIFEST is updated (Invariant 3).
    Status Truncate();

    size_t SizeBytes() const;
    void   Close();

private:
    struct PendingWrite {
        OpType      op;
        std::string key;
        std::string value;
        std::promise<Status> result;
    };

    Status SubmitAndWait(OpType op, const Slice& key, const Slice& value);
    void   WriterLoop();

    // Serialise a single record to a buffer.
    static void EncodeRecord(OpType op, const Slice& k, const Slice& v,
                              std::string& out);

    std::string  path_;
    Options      opts_;
    int          fd_{-1};

    std::mutex               mu_;
    std::condition_variable  cv_submit_;  // writers → thread
    std::condition_variable  cv_done_;    // thread → writers (unused; futures used)
    std::deque<PendingWrite*> queue_;
    bool                     shutdown_{false};
    std::thread              writer_;
};

}  // namespace lsmkv

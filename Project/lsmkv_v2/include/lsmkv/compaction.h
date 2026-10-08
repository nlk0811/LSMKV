#pragma once
/*
 * K-Way Merge Compaction
 * ──────────────────────
 * v2: Uses std::priority_queue<MergeEntry> — same algorithm as v1 but with
 * proper C++ types, Status return, and explicit tombstone-drop control.
 *
 * Complexity: O(N log K)  time
 *             O(K)        space  (heap holds one entry per file)
 */
#include "sstable.h"
#include "options.h"
#include "status.h"
#include <string>
#include <vector>

namespace lsmkv {

class BlockCache;

struct CompactionInput {
    std::vector<std::string> paths;  // newest first within each level
};

struct CompactionOutput {
    std::string path;
    size_t      entries_written{0};
};

// Merge all paths in `input` (newest-first within the list) into `output_path`.
// If drop_tombstones=true, tombstone entries are suppressed entirely
// (correct only at the deepest compaction level).
Status RunCompaction(const std::vector<std::string>& input_paths,
                     const std::string&              output_path,
                     const Options&                  opts,
                     BlockCache*                     cache,
                     bool                            drop_tombstones,
                     size_t*                         entries_written);

}  // namespace lsmkv

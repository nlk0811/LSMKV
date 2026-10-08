#pragma once
/*
 * MANIFEST
 * ─────────
 * Tracks which SSTable files exist at each level.
 * Atomic write: tmp → fdatasync → rename → fdatasync(dir)
 *
 * Invariant 2 (Compaction Atomicity):
 *   ApplyCompaction() is the commit point — a crash before this call leaves
 *   the old files; a crash after leaves the new file in the manifest.
 *
 * Invariant 3 (WAL Truncation Safety):
 *   AddL0() must be called BEFORE WAL::Truncate().
 */
#include "status.h"
#include "options.h"
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace lsmkv {

class Manifest {
public:
    explicit Manifest(const std::string& directory, const Options& opts);

    Status Load();

    // Register a freshly flushed L0 SSTable.  Call BEFORE WAL truncation.
    Status AddL0(const std::string& path);

    // Atomic compaction commit: remove inputs, add outputs.
    Status ApplyCompaction(const std::map<int, std::vector<std::string>>& inputs,
                           const std::map<int, std::vector<std::string>>& outputs);

    // Read-only accessors (caller must hold lock or rely on single-writer).
    const std::vector<std::string>& Level(int lvl) const { return levels_.at(lvl); }
    std::vector<std::vector<std::string>> Snapshot() const;
    std::vector<std::string> AllFiles() const;
    size_t LevelSize(int lvl) const;  // sum of file sizes on disk

private:
    Status Save();

    std::string  dir_;
    std::string  path_;
    Options      opts_;
    mutable std::mutex mu_;
    std::vector<std::vector<std::string>> levels_;
};

}  // namespace lsmkv

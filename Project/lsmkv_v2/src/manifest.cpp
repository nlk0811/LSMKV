#include "lsmkv/manifest.h"
#include <cassert>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <numeric>
#include <sstream>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __APPLE__
#ifndef F_FULLFSYNC
#define F_FULLFSYNC 51
#endif
#endif

namespace lsmkv {

static Status DurableSyncFd(int fd) {
#ifdef __APPLE__
    if (fcntl(fd, F_FULLFSYNC) == 0) return Status::OK();
#endif
    return (fsync(fd) == 0) ? Status::OK() : Status::IOError("fsync");
}

static Status DurableSyncDir(const std::string& dir) {
    int fd = open(dir.c_str(), O_RDONLY);
    if (fd < 0) return Status::IOError("open dir: " + dir);
    auto s = DurableSyncFd(fd);
    close(fd);
    return s;
}

// ── Simple JSON helpers ───────────────────────────────────────────────────────

static std::string ToJSON(const std::vector<std::vector<std::string>>& levels) {
    std::ostringstream out;
    out << "{\n  \"levels\": [\n";
    for (size_t i = 0; i < levels.size(); ++i) {
        out << "    [";
        for (size_t j = 0; j < levels[i].size(); ++j) {
            out << "\"" << levels[i][j] << "\"";
            if (j+1 < levels[i].size()) out << ", ";
        }
        out << "]";
        if (i+1 < levels.size()) out << ",";
        out << "\n";
    }
    out << "  ]\n}";
    return out.str();
}

static bool FileExists(const std::string& p) {
    struct stat st; return stat(p.c_str(), &st) == 0;
}

static std::vector<std::vector<std::string>> ParseJSON(const std::string& text,
                                                        int max_levels) {
    std::vector<std::vector<std::string>> levels(max_levels);
    // Simple parser: look for quoted strings inside each [...] array block
    size_t pos = 0;
    int lvl = -1;
    while (pos < text.size()) {
        if (text[pos] == '[') {
            ++lvl;
            if (lvl >= max_levels) break;
            ++pos;
            continue;
        }
        if (text[pos] == ']') {
            ++pos; continue;
        }
        if (text[pos] == '"' && lvl >= 0) {
            size_t end = text.find('"', pos+1);
            if (end == std::string::npos) break;
            std::string path = text.substr(pos+1, end-pos-1);
            if (FileExists(path)) levels[lvl].push_back(path);
            pos = end + 1;
            continue;
        }
        ++pos;
    }
    return levels;
}

// ── Manifest ──────────────────────────────────────────────────────────────────

Manifest::Manifest(const std::string& directory, const Options& opts)
    : dir_(directory), opts_(opts) {
    path_ = directory + "/MANIFEST.json";
    levels_.resize(opts.max_levels);
}

Status Manifest::Load() {
    if (!FileExists(path_)) return Status::OK();
    std::ifstream f(path_);
    std::string text((std::istreambuf_iterator<char>(f)),
                      std::istreambuf_iterator<char>());
    levels_ = ParseJSON(text, opts_.max_levels);
    return Status::OK();
}

Status Manifest::Save() {
    std::string tmp = path_ + ".tmp";
    {
        std::ofstream f(tmp);
        f << ToJSON(levels_);
    }
    int fd = open(tmp.c_str(), O_WRONLY);
    if (fd >= 0) { DurableSyncFd(fd); close(fd); }
    if (rename(tmp.c_str(), path_.c_str()) != 0)
        return Status::IOError("manifest rename failed");
    return DurableSyncDir(dir_);
}

Status Manifest::AddL0(const std::string& path) {
    std::lock_guard<std::mutex> lk(mu_);
    levels_[0].push_back(path);
    return Save();
}

Status Manifest::ApplyCompaction(
    const std::map<int, std::vector<std::string>>& inputs,
    const std::map<int, std::vector<std::string>>& outputs) {

    std::lock_guard<std::mutex> lk(mu_);
    for (auto& [lvl, paths] : inputs) {
        auto& v = levels_[lvl];
        for (auto& p : paths)
            v.erase(std::remove(v.begin(), v.end(), p), v.end());
    }
    for (auto& [lvl, paths] : outputs)
        for (auto& p : paths)
            levels_[lvl].push_back(p);
    return Save();
}

std::vector<std::vector<std::string>> Manifest::Snapshot() const {
    std::lock_guard<std::mutex> lk(mu_);
    return levels_;
}

std::vector<std::string> Manifest::AllFiles() const {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<std::string> all;
    for (auto& lvl : levels_)
        for (auto& f : lvl)
            all.push_back(f);
    return all;
}

size_t Manifest::LevelSize(int lvl) const {
    std::lock_guard<std::mutex> lk(mu_);
    size_t total = 0;
    for (auto& f : levels_[lvl]) {
        struct stat st;
        if (stat(f.c_str(), &st) == 0) total += static_cast<size_t>(st.st_size);
    }
    return total;
}

}  // namespace lsmkv

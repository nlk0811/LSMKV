#pragma once
#include <string>

namespace lsmkv {

class Status {
public:
    enum Code { kOk = 0, kNotFound, kIOError, kCorruption, kInvalidArg };

    Status() : code_(kOk) {}

    static Status OK()                        { return Status(kOk); }
    static Status NotFound(std::string msg)   { return Status(kNotFound,    std::move(msg)); }
    static Status IOError(std::string msg)    { return Status(kIOError,     std::move(msg)); }
    static Status Corruption(std::string msg) { return Status(kCorruption,  std::move(msg)); }
    static Status InvalidArg(std::string msg) { return Status(kInvalidArg,  std::move(msg)); }

    bool ok()          const { return code_ == kOk; }
    bool IsNotFound()  const { return code_ == kNotFound; }
    bool IsIOError()   const { return code_ == kIOError; }
    bool IsCorruption()const { return code_ == kCorruption; }

    Code        code()    const { return code_; }
    std::string message() const { return msg_; }

    std::string ToString() const {
        if (ok()) return "OK";
        const char* label[] = {"OK","NotFound","IOError","Corruption","InvalidArg"};
        return std::string(label[code_]) + ": " + msg_;
    }

private:
    Status(Code c, std::string msg = {}) : code_(c), msg_(std::move(msg)) {}
    Code        code_;
    std::string msg_;
};

}  // namespace lsmkv

"""Printer queue simulation — jobs processed in FIFO order."""

import time
from collections import deque


class Job:
    def __init__(self, jid, name, pages):
        self.jid = jid
        self.name = name
        self.pages = pages

    def __str__(self):
        return f"Job #{self.jid}: {self.name} ({self.pages} pages)"


class P:
    def __init__(self, name="Printer"):
        self.name = name
        self.q = deque()
        self.jid = 1
        self.done = []

    def empty(self):
        return len(self.q) == 0

    def add(self, name, pages):
        job = Job(self.jid, name, pages)
        self.jid += 1
        self.q.append(job)
        print(f"Added: {job}")
        return job

    def proc(self, delay=1):
        if self.empty():
            print("No jobs.")
            return None

        job = self.q.popleft()
        print(f"\nProcessing: {job}")
        print(f"Printing {job.pages}...")

        for page in range(1, job.pages + 1):
            print(f"  Page {page}/{job.pages} done.")
            time.sleep(delay)

        print(f"Completed: {job}\n")
        self.done.append(job)
        return job

    def proc_all(self, delay=1):
        if self.empty():
            print("No jobs.")
            return

        print(f"\n{self.name} — processing all.\n")
        while not self.empty():
            self.proc(delay=delay)

    def show(self):
        if self.empty():
            print("Queue empty.")
            return

        print(f"\n{self.name} — pending:")
        for i, job in enumerate(self.q, start=1):
            print(f"  {i}. {job}")
        print()


def main():
    pr = P("HP")

    print("Printer demo")
    print("Commands: add <doc> <pages>, proc, proc_all, show, exit\n")

    while True:
        cmd = input("> ").strip()
        if not cmd:
            continue

        parts = cmd.split()
        act = parts[0].lower()

        if act == "exit":
            print("Exit.")
            break
        elif act == "add":
            if len(parts) < 3:
                print("Usage: add <doc> <pages>")
                continue
            try:
                pages = int(parts[-1])
                name = " ".join(parts[1:-1])
                if pages <= 0:
                    print("Pages >0")
                    continue
                pr.add(name, pages)
            except ValueError:
                print("Usage: add <doc> <pages>")
        elif act == "proc":
            pr.proc(delay=0.2)
        elif act == "proc_all":
            pr.proc_all(delay=0.2)
        elif act in {"show", "display"}:
            pr.show()
        else:
            print("Unknown.")


if __name__ == "__main__":
    main()

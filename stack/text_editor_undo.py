"""Text editor Undo/Redo functionality using two stacks."""


class Ed:
    def __init__(self):
        self.txt = ""
        self.undo = []
        self.redo = []

    def type_(self, new):
        if not new:
            return
        self.undo.append(self.txt)
        self.txt += new
        self.redo.clear()
        print(f"Typed: {new!r}")

    def del_(self, n):
        if n <= 0:
            print("N must be positive.")
            return
        if n > len(self.txt):
            n = len(self.txt)

        self.undo.append(self.txt)
        rem = self.txt[-n:]
        self.txt = self.txt[:-n]
        self.redo.clear()
        print(f"Deleted: {rem!r}")

    def undo_(self):
        if not self.undo:
            print("Nothing to undo.")
            return

        self.redo.append(self.txt)
        self.txt = self.undo.pop()
        print("Undo done.")

    def redo_(self):
        if not self.redo:
            print("Nothing to redo.")
            return

        self.undo.append(self.txt)
        self.txt = self.redo.pop()
        print("Redo done.")

    def show(self):
        print(f"Text: {self.txt!r}")


def main():
    ed = Ed()

    print("Text Editor (undo/redo)")
    print("Commands: type <text>, delete <n>, undo, redo, show, exit\n")

    while True:
        cmd = input("> ").strip()
        if not cmd:
            continue

        parts = cmd.split(maxsplit=1)
        act = parts[0].lower()

        if act == "exit":
            print("Exit.")
            break
        elif act == "type":
            if len(parts) < 2:
                print("Usage: type <text>")
            else:
                ed.type_(parts[1])
        elif act == "delete":
            if len(parts) < 2 or not parts[1].isdigit():
                print("Usage: delete <n>")
            else:
                ed.del_(int(parts[1]))
        elif act == "undo":
            ed.undo_()
        elif act == "redo":
            ed.redo_()
        elif act in {"show", "display"}:
            ed.show()
        else:
            print("Unknown.")

        ed.show()


if __name__ == "__main__":
    main()

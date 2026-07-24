"""Text editor Undo/Redo functionality using two stacks."""


class TextEditor:
    def __init__(self):
        self.text = ""
        self.undo_stack = []
        self.redo_stack = []

    def type_text(self, new_text):
        if not new_text:
            return
        self.undo_stack.append(self.text)
        self.text += new_text
        self.redo_stack.clear()
        print(f"Typed: {new_text!r}")

    def delete_text(self, count):
        if count <= 0:
            print("Delete count must be positive.")
            return
        if count > len(self.text):
            count = len(self.text)

        self.undo_stack.append(self.text)
        removed = self.text[-count:]
        self.text = self.text[:-count]
        self.redo_stack.clear()
        print(f"Deleted: {removed!r}")

    def undo(self):
        if not self.undo_stack:
            print("Nothing to undo.")
            return

        self.redo_stack.append(self.text)
        self.text = self.undo_stack.pop()
        print("Undo successful.")

    def redo(self):
        if not self.redo_stack:
            print("Nothing to redo.")
            return

        self.undo_stack.append(self.text)
        self.text = self.redo_stack.pop()
        print("Redo successful.")

    def display(self):
        print(f"Current text: {self.text!r}")


def main():
    editor = TextEditor()

    print("Text Editor with Undo/Redo (Two Stacks)")
    print("Commands: type <text>, delete <n>, undo, redo, show, exit\n")

    while True:
        command = input("> ").strip()
        if not command:
            continue

        parts = command.split(maxsplit=1)
        action = parts[0].lower()

        if action == "exit":
            print("Exiting editor.")
            break
        elif action == "type":
            if len(parts) < 2:
                print("Usage: type <text>")
            else:
                editor.type_text(parts[1])
        elif action == "delete":
            if len(parts) < 2 or not parts[1].isdigit():
                print("Usage: delete <number of characters>")
            else:
                editor.delete_text(int(parts[1]))
        elif action == "undo":
            editor.undo()
        elif action == "redo":
            editor.redo()
        elif action in {"show", "display"}:
            editor.display()
        else:
            print("Unknown command.")

        editor.display()


if __name__ == "__main__":
    main()

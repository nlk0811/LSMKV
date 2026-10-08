"""Queue implementation using two stacks."""


class St:
    def __init__(self):
        self.items = []

    def empty(self):
        return len(self.items) == 0

    def push(self, v):
        self.items.append(v)

    def pop(self):
        if self.empty():
            return None
        return self.items.pop()

    def peek(self):
        if self.empty():
            return None
        return self.items[-1]


class Q2:
    def __init__(self):
        self.en = St()
        self.de = St()

    def empty(self):
        return self.en.empty() and self.de.empty()

    def xfer(self):
        if self.de.empty():
            while not self.en.empty():
                self.de.push(self.en.pop())

    def enq(self, v):
        self.en.push(v)
        print(f"Enqueued: {v}")

    def deq(self):
        if self.empty():
            print("Queue empty!")
            return None

        self.xfer()
        v = self.de.pop()
        print(f"Dequeued: {v}")
        return v

    def front(self):
        if self.empty():
            print("Queue empty.")
            return None

        self.xfer()
        return self.de.peek()

    def rear(self):
        if self.empty():
            print("Queue empty.")
            return None

        if not self.en.empty():
            return self.en.peek()

        tmp = St()
        bot = None
        while not self.de.empty():
            bot = self.de.pop()
            tmp.push(bot)

        while not tmp.empty():
            self.de.push(tmp.pop())

        return bot

    def show(self):
        if self.empty():
            print("Queue empty.")
            return

        t1 = St()
        t2 = St()
        elems = []

        while not self.de.empty():
            v = self.de.pop()
            elems.append(str(v))
            t1.push(v)

        while not t1.empty():
            self.de.push(t1.pop())

        back = []
        while not self.en.empty():
            v = self.en.pop()
            back.append(str(v))
            t2.push(v)

        while not t2.empty():
            self.en.push(t2.pop())

        elems.extend(reversed(back))
        print("Queue (front -> rear):", " <- ".join(elems))


def main():
    q = Q2()

    while True:
        print("\n--- Two-Stack Queue Menu ---")
        print("1. Enq")
        print("2. Deq")
        print("3. Front")
        print("4. Rear")
        print("5. Show")
        print("6. Exit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            v = input("Enter value: ").strip()
            q.enq(v)
        elif choice == "2":
            q.deq()
        elif choice == "3":
            e = q.front()
            if e is not None:
                print(f"Front: {e}")
        elif choice == "4":
            e = q.rear()
            if e is not None:
                print(f"Rear: {e}")
        elif choice == "5":
            q.show()
        elif choice == "6":
            print("Exit.")
            break
        else:
            print("Invalid.")


if __name__ == "__main__":
    main()

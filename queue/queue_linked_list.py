"""Queue implementation using linked lists."""


class N:
    def __init__(self, d):
        self.d = d
        self.nxt = None


class QueueL:
    def __init__(self):
        self.f = None
        self.r = None
        self.n = 0

    def empty(self):
        return self.f is None

    def enq(self, value):
        node = N(value)
        if self.empty():
            self.f = node
        else:
            self.r.nxt = node
        self.r = node
        self.n += 1
        print(f"Enqueued: {value}")

    def deq(self):
        if self.empty():
            print("Queue empty!")
            return None

        value = self.f.d
        self.f = self.f.nxt

        if self.f is None:
            self.r = None

        self.n -= 1
        print(f"Dequeued: {value}")
        return value

    def front(self):
        if self.empty():
            print("Queue empty.")
            return None
        return self.f.d

    def rear(self):
        if self.empty():
            print("Queue empty.")
            return None
        return self.r.d

    def show(self):
        if self.empty():
            print("Queue empty.")
            return

        elems = []
        cur = self.f
        while cur:
            elems.append(str(cur.d))
            cur = cur.nxt
        print("Queue (front -> rear):", " <- ".join(elems))


def main():
    q = QueueL()

    while True:
        print("\n--- Linked List Queue Menu ---")
        print("1. Enq")
        print("2. Deq")
        print("3. Front")
        print("4. Rear")
        print("5. Show")
        print("6. Exit")
        choice = input("Enter choice: ").strip()

        if choice == "1":
            value = input("Enter value: ").strip()
            q.enq(value)
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

# Q1: Word Frequency Counter using dict

sentence = "the quick brown fox jumps over the lazy dog the fox"
freq = {}
for word in sentence.split():
    word = word.strip(".,!?;:")
    if word in freq:
        freq[word] += 1
    else:
        freq[word] = 1

print("Word Frequencies:")
for word in freq:
    print(word, ":", freq[word])


# Q2: Student Marks Dictionary

marks = {
    "Alice": 85,
    "Bob": 92,
    "Charlie": 78
}

# (i) Insert 4th student
marks["Diana"] = 95
print("\nAfter inserting Diana:", marks)

# (ii) Find students with marks above 90
print("\nStudents with marks above 90:")
for name in marks:
    if marks[name] > 90:
        print(name, ":", marks[name])

# (iii) Update 2nd student to 100
keys = list(marks.keys())
marks[keys[1]] = 100
print("\nAfter updating", keys[1], "to 100:", marks)


# Q3: Skip List for student database

import random

MAX_LEVEL = 4

class SkipNode:
    def __init__(self, key, value, level):
        self.key = key
        self.value = value
        self.forward = [None] * (level + 1)

class SkipList:
    def __init__(self):
        self.level = 0
        self.head = SkipNode(float('-inf'), None, MAX_LEVEL)

    def randomLevel(self):
        lvl = 0
        while random.random() < 0.5 and lvl < MAX_LEVEL:
            lvl += 1
        return lvl

    def insert(self, key, value):
        update = [None] * (MAX_LEVEL + 1)
        current = self.head
        i = self.level
        while i >= 0:
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]
            update[i] = current
            i -= 1

        current = current.forward[0]
        if current and current.key == key:
            current.value = value
            return

        newLevel = self.randomLevel()
        if newLevel > self.level:
            j = self.level + 1
            while j <= newLevel:
                update[j] = self.head
                j += 1
            self.level = newLevel

        newNode = SkipNode(key, value, newLevel)
        i = 0
        while i <= newLevel:
            newNode.forward[i] = update[i].forward[i]
            update[i].forward[i] = newNode
            i += 1

    def search(self, key):
        current = self.head
        i = self.level
        while i >= 0:
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]
            i -= 1
        current = current.forward[0]
        if current and current.key == key:
            return current.value
        return None

    def display(self):
        current = self.head.forward[0]
        while current:
            print(current.key, ":", current.value)
            current = current.forward[0]


sl = SkipList()
sl.insert(85, "Alice")
sl.insert(92, "Bob")
sl.insert(78, "Charlie")
sl.insert(95, "Diana")
sl.insert(88, "Eve")

print("\nSkip List (sorted by marks):")
sl.display()

print("\nSearch 92:", sl.search(92))
print("Search 70:", sl.search(70))

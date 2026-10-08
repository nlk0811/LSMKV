def insertion(marks):
    for i in range(1, len(marks)):
        current_marks = marks[i]

        j = i-1

        while j >= 0 and marks[j] > current_marks:
            marks[j+1] = marks[j]
            j -= 1

        marks[j+1] = current_marks

    return marks

n = [72, 89, 65, 94, 78] 

a = insertion(n)

print(a)
def mergesort(arr):
    if len(arr) <=1:
        return arr

    mid = len(arr)//2
    
    left = mergesort(arr[:mid])
    right = mergesort(arr[mid:])

    result = []

    i = j = 0
    while i < len(left) and j < len(right):
        if left[i]>right[j]:
            result.append(right[j])
            j += 1
        else:
            result.append(left[i])
            i += 1
    result += left[i:]
    result += right[j:]
    return result

a = [89,23,1,45,25,90,3,17,5]
print(mergesort(a))
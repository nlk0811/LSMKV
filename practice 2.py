def quicksort(arr,lo,hi):
    if lo>hi:
        return arr

    pivot = arr[hi]
    i = lo

    for j in range(lo,hi):
        if arr[j] < pivot:
            arr[i], arr[j] = arr[j], arr[i]
            i +=1

    arr[i], arr[hi] = arr[hi], arr[i]
    return quicksort(arr,lo,i-1), quicksort(arr,i+1,hi)


a = [89,23,1,45,25,90,3,17,5]
b = quicksort(a,0,len(a)-1)

print(b)
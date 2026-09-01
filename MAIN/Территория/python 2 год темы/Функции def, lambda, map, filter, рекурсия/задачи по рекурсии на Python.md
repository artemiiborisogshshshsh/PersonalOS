# Задания
## 🟢 **I. Базовый уровень (понятие рекурсии)**

1. **Сумма чисел от 1 до n**  
    Напиши функцию `sum_n(n)`, которая возвращает сумму всех чисел от 1 до $n$
2. **Факториал числа**  
    Функция `fact(n)` возвращает $n! = 1 \cdot 2 \cdot 3 \cdot \ldots \cdot n$
3. **Вывод чисел от 1 до n**  
    Напиши функцию `print_numbers(n)`, которая рекурсивно выводит все числа от 1 до $n$
4. **Числа Фибоначчи**  
    Функция `fib(n)` возвращает $n$-е число Фибоначчи.  
    Пример: $$fib(6) = 8$$
5. **Подсчёт цифр числа**  
    Функция `count_digits(n)` возвращает количество цифр в числе $n$
6. **Сумма цифр числа**  
    Функция `sum_digits(n)` возвращает сумму всех цифр числа $n$
## 🟡 **II. Средний уровень (работа с массивами и строками)**

1. **Разворот строки**  
    Функция `reverse(s)` возвращает строку $s$ в обратном порядке.
2. **Проверка палиндрома**  
    Функция `is_palindrome(s)` проверяет, является ли строка палиндромом.
3. **Поиск максимума в списке**  
    Функция `max_rec(lst)` возвращает максимальный элемент списка.
4. **Подсчёт количества элементов в списке**  
    Функция `count(lst)` возвращает длину списка без использования `len()`.
5. **Сумма элементов списка**  
    Функция `sum_list(lst)` возвращает сумму всех элементов списка.
6. **Подсчёт количества вхождений элемента**  
    Функция `count_occurrences(lst, x)` считает, сколько раз элемент $x$ встречается в списке.
## 🔵 **III. Продвинутый уровень (разветвлённая рекурсия и комбинирование)**
1. **Быстрое возведение в степень**  
    Функция `power(a, n)` вычисляет $a^n$ с помощью рекурсии и свойства:  
    $a^n = (a^{n/2})^2$, если $n$ чётное, иначе $a \cdot a^{n-1}$.
2. **Число путей на сетке**  
    Функция `paths(m, n)` возвращает количество путей из левого верхнего угла сетки $m \times n$ в правый нижний, если можно двигаться только вправо и вниз.
3. **Перестановки символов строки**  
    Функция `permutations(s)` возвращает все перестановки символов строки $s$.
4. **Сочетания из n по k**  
    Функция `C(n, k)` возвращает количество сочетаний $C_n^k$ по формуле:  
    $C(n, k) = C(n-1, k-1) + C(n-1, k)$. 
5. **Ханойская башня**  
    Функция `hanoi(n, source, target, auxiliary)` выводит шаги для перемещения $n$ дисков с одного стержня на другой.
6. **Рекурсивный бинарный поиск**  
    Функция `binary_search(arr, x, left, right)` ищет элемент $x$ в отсортированном списке.

<div class="page-break" style="page-break-before: always;"></div>

# Ответы
## 🟢 **I. БАЗОВЫЙ УРОВЕНЬ**
### **1. Сумма чисел от 1 до n**
**Задание:**  
Напиши функцию `sum_n(n)`, которая возвращает сумму всех чисел от 1 до $n$.
**Решение:**
```python
def sum_n(n):
    if n == 1:
        return 1
    return n + sum_n(n - 1)

print(sum_n(5))  # 15
```
### **2. Факториал числа**
**Задание:**  
Реализуй функцию `fact(n)`, вычисляющую $n!$.
**Решение:**
```python
def fact(n):
    if n == 1:
        return 1
    return n * fact(n - 1)

print(fact(5))  # 120
```
### **3. Вывод чисел от 1 до n**
**Задание:**  
Напиши функцию, которая выводит числа от 1 до $n$.
**Решение:**
```python
def print_numbers(n):
    if n == 0:
        return
    print_numbers(n - 1)
    print(n, end=' ')

print_numbers(5)  # 1 2 3 4 5
```
### **4. Число Фибоначчи**
**Задание:**  
Напиши функцию `fib(n)`, возвращающую $n$-е число Фибоначчи.
**Решение:**
```python
def fib(n):
    if n <= 2:
        return 1
    return fib(n - 1) + fib(n - 2)

print(fib(6))  # 8
```
### **5. Количество цифр числа**
**Задание:**  
Функция `count_digits(n)` возвращает количество цифр в числе.
**Решение:**
```python
def count_digits(n):
    if n < 10:
        return 1
    return 1 + count_digits(n // 10)

print(count_digits(12345))  # 5
```
### **6. Сумма цифр числа**
**Задание:**  
Функция `sum_digits(n)` возвращает сумму цифр числа.
**Решение:**
```python
def sum_digits(n):
    if n < 10:
        return n
    return n % 10 + sum_digits(n // 10)

print(sum_digits(1234))  # 10
```
## 🟡 **II. СРЕДНИЙ УРОВЕНЬ**
### **1. Разворот строки**
**Задание:**  
Функция `reverse(s)` возвращает строку в обратном порядке.
**Решение:**
```python
def reverse(s):
    if len(s) == 0:
        return ''
    return s[-1] + reverse(s[:-1])

print(reverse("abcde"))  # "edcba"
```
### **2. Проверка палиндрома**
**Задание:**  
Функция `is_palindrome(s)` возвращает `True`, если строка палиндром.
**Решение:**
```python
def is_palindrome(s):
    if len(s) <= 1:
        return True
    if s[0] != s[-1]:
        return False
    return is_palindrome(s[1:-1])

print(is_palindrome("level"))  # True
```
### **3. Поиск максимума в списке**
**Задание:**  
Функция `max_rec(lst)` возвращает максимальный элемент.
**Решение:**
```python
def max_rec(lst):
    if len(lst) == 1:
        return lst[0]
    m = max_rec(lst[1:])
    return lst[0] if lst[0] > m else m

print(max_rec([1, 8, 3, 5]))  # 8
```
### **4. Подсчёт длины списка**
**Задание:**  
Функция `count(lst)` возвращает длину списка без `len()`.
**Решение:**
```python
def count(lst):
    if lst == []:
        return 0
    return 1 + count(lst[1:])

print(count([1, 2, 3, 4]))  # 4
```
### **5. Сумма элементов списка**
**Задание:**  
Функция `sum_list(lst)` возвращает сумму элементов.
**Решение:**
```python
def sum_list(lst):
    if not lst:
        return 0
    return lst[0] + sum_list(lst[1:])

print(sum_list([1, 2, 3, 4]))  # 10
```
### **6. Подсчёт вхождений элемента**
**Задание:**  
`count_occurrences(lst, x)` — количество вхождений $x$ в список.
**Решение:**
```python
def count_occurrences(lst, x):
    if not lst:
        return 0
    return (1 if lst[0] == x else 0) + count_occurrences(lst[1:], x)

print(count_occurrences([1, 2, 3, 2, 2], 2))  # 3
```
## 🔵 **III. ПРОДВИНУТЫЙ УРОВЕНЬ**
### **1. Быстрое возведение в степень**
**Задание:**  
Функция `power(a, n)` вычисляет $a^n$.
**Решение:**
```python
def power(a, n):
    if n == 0:
        return 1
    if n % 2 == 0:
        half = power(a, n // 2)
        return half * half
    return a * power(a, n - 1)

print(power(2, 10))  # 1024
```
### **2. Количество путей на сетке m×n**
**Задание:**  
Можно двигаться только вправо и вниз.
**Решение:**
```python
def paths(m, n):
    if m == 1 or n == 1:
        return 1
    return paths(m - 1, n) + paths(m, n - 1)

print(paths(3, 3))  # 6
```
### **3. Перестановки строки**
**Задание:**  
Функция `permutations(s)` возвращает все перестановки.
**Решение:**
```python
def permutations(s):
    if len(s) <= 1:
        return [s]
    result = []
    for i in range(len(s)):
        for p in permutations(s[:i] + s[i+1:]):
            result.append(s[i] + p)
    return result

print(permutations("abc"))
```
### **4. Сочетания C(n, k)**
**Задание:**  
Функция `C(n, k)` возвращает $C_n^k$.
**Решение:**
```python
def C(n, k):
    if k == 0 or k == n:
        return 1
    return C(n - 1, k - 1) + C(n - 1, k)

print(C(5, 2))  # 10
```
### **5. Ханойская башня**

**Задание:**  
Вывести шаги для перемещения $n$ дисков.
**Решение:**
```python
def hanoi(n, source, target, auxiliary):
    if n == 1:
        print(f"{source} → {target}")
        return
    hanoi(n - 1, source, auxiliary, target)
    print(f"{source} → {target}")
    hanoi(n - 1, auxiliary, target, source)

hanoi(3, 'A', 'C', 'B')
```
### **6. Рекурсивный бинарный поиск**

**Задание:**  
Ищет элемент $x$ в отсортированном списке.
**Решение:**

```python
def binary_search(arr, x, left, right):
    if left > right:
        return -1
    mid = (left + right) // 2
    if arr[mid] == x:
        return mid
    elif arr[mid] > x:
        return binary_search(arr, x, left, mid - 1)
    else:
        return binary_search(arr, x, mid + 1, right)

print(binary_search([1, 3, 5, 7, 9], 7, 0, 4))  # 3
```
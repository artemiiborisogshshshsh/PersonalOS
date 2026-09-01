---
tags: [python, map, filter, lambda, задачи]
topic: Задания map, filter, lambda — от базового до продвинутого уровня
connections: [КОНТРОЛЬНАЯ РАБОТА, Часть 5 подготовки к ЕГЭ]
---

## 🔹 Уровень 1 — Базовые

1. Используя `map`, преобразуй список строк в список их длин.
   ```python
   words = ["apple", "banana", "cherry"]
   ```

2. Используя `filter`, оставь только чётные числа из списка.
   ```python
   nums = [1, 2, 3, 4, 5, 6]
   ```

3. Используя `lambda`, вычисли квадрат каждого числа с помощью `map`.
   ```python
   nums = [2, 4, 6, 8]
   ```

4. С помощью `filter` и `lambda` оставь слова длиннее 5 символов.
   ```python
   words = ["cat", "elephant", "dog", "giraffe"]
   ```

5. Используя `map`, добавь к каждому числу 10.
   ```python
   nums = [1, 5, 10, 15]
   ```
### ✅ Ответы:
```python
list(map(len, words))  # [5, 6, 6]
list(filter(lambda x: x % 2 == 0, nums))  # [2, 4, 6]
list(map(lambda x: x ** 2, nums))  # [4, 16, 36, 64]
list(filter(lambda w: len(w) > 5, words))  # ['elephant', 'giraffe']
list(map(lambda x: x + 10, nums))  # [11, 15, 20, 25]
```

---

## 🔸 Уровень 2 — Средние

1. Используя `filter`, выбери из списка чисел только положительные.
   ```python
   numbers = [-3, 0, 2, 5, -1, 7]
   ```

2. Используя `map` и `lambda`, прибавь к каждому числу его индекс.
   ```python
   nums = [10, 20, 30, 40]
   ```

3. Используя `filter`, выбери строки, начинающиеся с буквы `"a"`.
   ```python
   words = ["apple", "banana", "avocado", "pear"]
   ```

4. Используя `map`, переведи список температур из Цельсия в Фаренгейты.  F = C * 9/5 + 32
   ```python
   temps_c = [0, 10, 20, 30]
   ```

### ✅ Ответы:
```python
list(filter(lambda x: x > 0, numbers))  # [2, 5, 7]
list(map(lambda x_i: x_i[1] + x_i[0], enumerate(nums)))  # [10, 21, 32, 43]
list(filter(lambda w: w.startswith("a"), words))  # ['apple', 'avocado']
list(map(lambda c: c * 9/5 + 32, temps_c))  # [32.0, 50.0, 68.0, 86.0]
```

---

## 🔹 Уровень 3 — Продвинутые
1. Используя `filter` и `lambda`, выбери палиндромы из списка.
   ```python
   words = ["level", "apple", "radar", "world"]
   ```

2. Используя `map`, переведи список чисел в строки с добавлением `"!"`.
   ```python
   nums = [1, 2, 3, 4]
   ```

3. Используя `filter`, выбери числа, которые делятся и на 2, и на 3.
   ```python
   nums = list(range(1, 21))
   ```

4. Используя `map` и `filter`, вычисли квадраты всех нечётных чисел.
   ```python
   nums = [1, 2, 3, 4, 5, 6]
   ```

### ✅ Ответы:
```python
list(filter(lambda w: w == w[::-1], words))  # ['level', 'radar']
list(map(lambda x: str(x) + "!", nums))  # ['1!', '2!', '3!', '4!']
list(filter(lambda x: x % 2 == 0 and x % 3 == 0, nums))  # [6, 12, 18]
list(map(lambda x: x**2, filter(lambda x: x % 2 != 0, nums)))  # [1, 9, 25]
```

---

## 🔸 Уровень 4 — Сложные (Комбинированные)

1. Используя `map` и `filter`, из списка списков выбери только те, где сумма элементов > 10, и умножь каждый элемент на 2.
   ```python
   pairs = [[1, 2], [5, 6], [10, 0], [3, 3]]
   ```

2. Используя `map` и `lambda`, из списка слов сделай список их последних букв.
   ```python
   words = ["python", "java", "c++", "haskell"]
   ```

3. Используя `map` и `lambda`, сформируй список из строк формата `"x -> x^2"`.
   ```python
   nums = [1, 2, 3, 4]
   ```

4. Используя `filter` и `map`, из списка слов оставь только те, что длиннее 4, и переведи их в верхний регистр.
   ```python
   words = ["sun", "planet", "galaxy", "star"]
   ```

### ✅ Ответы:
```python
list(map(lambda t: (t[0]*2, t[1]*2), filter(lambda t: sum(t) > 10, pairs)))
# [(10, 12), (20, 0)]

list(map(lambda w: w[-1], words))  # ['n', 'a', '+', 'l']

list(map(lambda x: f"{x} -> {x**2}", nums))  # ['1 -> 1', '2 -> 4', '3 -> 9', '4 -> 16']

list(map(str.upper, filter(lambda w: len(w) > 4, words)))  # ['PLANET', 'GALAXY']
```

## Связи
- [[КОНТРОЛЬНАЯ РАБОТА]] — основные задания по lambda, map, filter
- [[Часть 5 подготовки к ЕГЭ]] — теория map и filter для ЕГЭ

## Вариант 1
1. Что выведет программа?
```python
def say_hi():
    return "Привет!"

print(say_hi(), "мир!")
```
а) Привет!  
б) Привет! мир!  
в) Ошибка
2. Что напечатает программа?
```python
def double(x):
    print(x * 2)

result = double(5)
print(result)
```
а) 10  
б) None  
в) Ошибка
3. Напиши функцию `greet_user()`, которая печатает:  
"Добро пожаловать, друг!"
4. Напиши функцию `subtract(a, b)`, возвращающую результат вычитания `a - b`.
5. Функция `long_word(word)` возвращает `True`, если длина слова больше 5.
6. Напиши функцию `sum_to_n(n)`, возвращающую сумму чисел от 1 до n.
7. Функция `check_password(password)` возвращает  
    "Сильный", если длина ≥ 8,  
    "Слабый" – если меньше.
8. Функция `odd_squares(numbers)` возвращает новый список – квадраты нечётных чисел.
9. Функция `swap_case(text)` меняет регистр всех букв.
10⭐. Функция `mirror_words(sentence)` переворачивает каждое слово,  
но сохраняет порядок слов.  
Пример:  
`"Я люблю питон"` → `"Я юблюл нотип"

<div class="page-break" style="page-break-before: always;"></div>
## Вариант 2
1. Что выведет программа?
```python
def add(a, b):
    return a + b

print(add(2, 3) * 2)
```
а) 10  
б) 5  
в) Ошибка
2. Что вернёт функция?
```python
def test():
    print("Python")
    return 123

print(test())
```
а) Python  
б) Python и 123  
в) Только 123
3. Напиши функцию `say_hello(name)`, которая печатает `"Привет, <имя>!"`.
4. Напиши функцию `multiply(a, b)`, возвращающую произведение чисел.
5. Функция `short_word(word)` возвращает `True`, если длина слова меньше 4.
6. Функция `sum_list(lst)` возвращает сумму всех чисел в списке.
7. Функция `grade(mark)` возвращает  
    "Отлично", если оценка ≥ 5,  
    иначе "Учись ещё!".
8. Функция `even_numbers(numbers)` возвращает список только чётных чисел.
9. Функция `reverse_text(text)` возвращает строку в обратном порядке.
10⭐. Функция `join_words(words)` принимает список слов и соединяет их через пробел.
<div class="page-break" style="page-break-before: always;"></div>
## Вариант 3
1. Что выведет программа?
```python
def info(name, age):
    return f"{name} - {age} лет"

print(info("Катя", 12))
```
а) Катя - 12 лет  
б) name - age лет  
в) Ошибка
2. Что напечатает программа?
```python
def show():
    print("Учусь Python!")

x = show()
print(x)
```
а) Учусь Python!  
б) Учусь Python! и None  
в) Ошибка
3. Напиши функцию `triple(n)`, возвращающую число, умноженное на 3.
4. Напиши функцию `divide(a, b)`, возвращающую результат деления `a / b`.
5. Функция `contains_python(text)` возвращает `True`, если в тексте есть слово "python".
6. Функция `sum_digits(n)` возвращает сумму всех цифр числа.
7. Функция `positive_only(numbers)` возвращает список только положительных чисел.
8. Функция `capitalize_words(sentence)` делает каждое слово с заглавной буквы.
9. Функция `last_char(word)` возвращает последнюю букву слова.
10⭐. Функция `word_stats(sentence)` возвращает два числа — количество слов и общее число букв.
<div class="page-break" style="page-break-before: always;"></div>
# Ответы
### Вариант 1
1 – б  
2 – б/а/а,б
```python
def greet_user(): print("Добро пожаловать, друг!") 
def subtract(a,b): return a - b
def long_word(w): return len(w) > 5
def sum_to_n(n): return sum(range(1, n+1))
def check_password(p): return "Сильный" if len(p) >= 8 else "Слабый"
def odd_squares(nums): return [x**2 for x in nums if x % 2 != 0]
def swap_case(text): return ''.join(ch.lower() if ch.isupper() else ch.upper() for ch in text)
def mirror_words(sentence): return ' '.join(w[::-1] for w in sentence.split())
```
### Вариант 2
1 – а  
2 – б
```python
def say_hello(name): print(f"Привет, {name}!")
def multiply(a,b): return a * b
def short_word(w): return len(w) < 4
def sum_list(lst): return sum(lst)
def grade(mark): return "Отлично" if mark >= 5 else "Учись ещё!"
def even_numbers(nums): return [x for x in nums if x % 2 == 0]
def reverse_text(text): return text[::-1]
def join_words(words): return ' '.join(words)
```
### Вариант 3
1 – а  
2 – б
```python
def triple(n): return n * 3
def divide(a,b): return a / b
def contains_python(text): return "python" in text.lower()
def sum_digits(n): return sum(int(i) for i in str(n))
def positive_only(nums): return [x for x in nums if x > 0]
def capitalize_words(s): return ' '.join(w.capitalize() for w in s.split())
def last_char(w): return w[-1]
def word_stats(sentence):
    words = sentence.split()
    return len(words), sum(len(w) for w in words)
```
# Шкала оценивания

|Уровень|Баллы|Критерий|
|:-:|:-:|:--|
|**Отлично (5)**|18–20|Все задания выполнены правильно, в том числе со звёздочкой|
|**Хорошо (4)**|14–17|Допущены мелкие ошибки, но логика функций верна|
|**Удовлетворительно (3)**|9–13|Выполнены простые задания, но есть ошибки в логике|
|**Неудовлетворительно (2)**|0–8|Выполнено менее половины заданий или много синтаксических ошибок|
# Подсчёт баллов
- Задания 1–2 (тесты) — **по 1 баллу**
- Задания 3–9 (код) — **по 2 балла**
- Задание 10⭐ — **3 балла**
**Максимум: 19 баллов**
# Шкала оценивания

|           Уровень           | Баллы | Критерий                                                         |
| :-------------------------: | :---: | :--------------------------------------------------------------- |
|       **Отлично (5)**       | 12–14 | Все задания выполнены правильно, в том числе со звёздочкой       |
|       **Хорошо (4)**        | 9–11  | Допущены мелкие ошибки, но логика функций верна                  |
|  **Удовлетворительно (3)**  | 9–13  | Выполнены простые задания, но есть ошибки в логике               |
| **Неудовлетворительно (2)** |  0–8  | Выполнено менее половины заданий или много синтаксических ошибок |
# Подсчёт баллов
- Задания 1–2 (тесты) — **по 1 баллу**
- Задания 3–8 (код) — **по 2 балла**
- Задание 10⭐ — **3 балла (дополнительный)**
**Максимум: 14 баллов**
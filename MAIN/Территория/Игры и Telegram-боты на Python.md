# Оглавление

1. [[#1. namer_random.py|namer_random.py]] — консольная игра «Угадай число».
2. [[#2. quiz.py|quiz.py]] — консольная викторина с фиксированными вопросами.
3. [[#3. tic_tac_toe_no_pc.py|tic_tac_toe_no_pc.py]] — классические крестики-нолики для двух игроков.
4. [[#4. tic_tac_toe_pc.py|tic_tac_toe_pc.py]] — крестики-нолики с простым компьютером (случайный ход).
5. [[#5. фы.py|фы.py]] — вариант крестиков-ноликов с координатным вводом и случайным AI.
6. [[#6. new123.py|new123.py]] — игра на память на Pygame: открывание цветных кругов.
7. [[#7. б.py|б.py]] — примитивный прототип «Дино Прыгун» на Pygame.
8. [[#8. eg.py|eg.py]] — анимация колобка и лисы (без взаимодействия).
9. [[#9. pygme.py|pygme.py (фрагмент с падающими звёздами)]] — простейшая аркада на Pygame.
10. [[#10. main121.py|main121.py]] — симулятор светофора с движущимися машинами.
11. [[#11. quiz_pygame.py|quiz_pygame.py]] — викторина на Pygame с таймером и графическими кнопками.
12. [[#12. virtual_plant_shapes.py|virtual_plant_shapes.py]] — симулятор роста растения.
13. [[#13. cristmacs.py|cristmacs.py]] — новогодняя аркада: сбор подарков, уклонение от снежинок.
14. [[#14. se.py|se.py]] — усложнённая версия «Санта раздаёт подарки» с преградами.
15. [[#15. dino.py|dino.py]] — полноценный клон «Дино Прыгун»: гравитация, препятствия, смена дня/ночи.
16. [[#16. car.py|car.py]] — гоночная игра на Pygame с выбором сложности.
17. [[#17. sea_fight.py|sea_fight.py]] — упрощённая версия морского боя на Pygame.
18. [[#18. X0X.py|X0X.py]] — крестики-нолики на Pygame с умным AI, меню выбора режима.
19. [[#19. new_sea_fight.py|new_sea_fight.py]] — полноценный морской бой с AI, маскировкой кораблей.
20. [[#20. tetris.py|tetris.py]] — тетрис на Pygame: вращение фигур, заполнение линий.
21. [[#21. tgbot2.py|tgbot2.py]] — Telegram-бот «Угадай число».
22. [[#22. пара.py|пара.py]] — Telegram-бот-викторина (незаконченный).
23. [[#23. tg bot.py|tg bot.py]] — расширенный шаблон Telegram-бота с викториной и статистикой.
# Игры
## 1. namer_random.py
```python
import random


def game():
    number_to_guess = random.randint(1, 100)
    guess = None
    tries = 0

    while guess != number_to_guess:
        guess = int(input("Угадай число от 1 до 100: "))
        tries += 1
        if guess < number_to_guess:
            print("Слишком мало! Попробуй снова.")
        elif guess > number_to_guess:
            print("Слишком много! Попробуй снова.")

    print(f"Поздравляю! Ты угадал число {number_to_guess} за {tries} попыток.")


if __name__ == "__main__":
    game()

---

## 1. namer_random.py
```python
import random


def game():
    number_to_guess = random.randint(1, 100)
    guess = None
    tries = 0

    while guess != number_to_guess:
        guess = int(input("Угадай число от 1 до 100: "))
        tries += 1
        if guess < number_to_guess:
            print("Слишком мало! Попробуй снова.")
        elif guess > number_to_guess:
            print("Слишком много! Попробуй снова.")

    print(f"Поздравляю! Ты угадал число {number_to_guess} за {tries} попыток.")


if __name__ == "__main__":
    game()
## 1. namer_random.py
```python
import random


def game():
    number_to_guess = random.randint(1, 100)
    guess = None
    tries = 0

    while guess != number_to_guess:
        guess = int(input("Угадай число от 1 до 100: "))
        tries += 1
        if guess < number_to_guess:
            print("Слишком мало! Попробуй снова.")
        elif guess > number_to_guess:
            print("Слишком много! Попробуй снова.")

    print(f"Поздравляю! Ты угадал число {number_to_guess} за {tries} попыток.")


if __name__ == "__main__":
    game()
```

## 2. quiz.py
```python
def quiz():
    questions = {
        "Какой язык программирования используется для веб-разработки?": "Python",
        "Какой язык программирования лучше всего подходит для анализа данных?": "R",
        "Кто создал язык программирования Python?": "Гвидо ван Россум",
        "Что такое OOP в программировании?": "Объектно-ориентированное программирование",
        "Какая структура данных подходит для хранения пар ключ-значение?": "Словарь",
        "Что такое операционная система?": "Программное обеспечение, управляющее компьютером.",
        "Что такое Интернет?": "Глобальная сеть компьютеров.",
        "Что такое браузер?": "Программа для просмотра веб-страниц.",
        "Что такое компьютерная графика?": "Создание изображений с помощью компьютера.",
        "Что защитит компьютер от вирусов?": "Антивирусная программа.",
    }

    correct_answers = 0

    print("Добро пожаловать в викторину! Ответьте на следующие вопросы:\n")

    for question, correct_answer in questions.items():
        print(question)
        user_answer = input("Ваш ответ: ")
        if user_answer.strip().lower() == correct_answer.lower():
            print("Правильно!\n")
            correct_answers += 1
        else:
            print(f"Неправильно! Правильный ответ: {correct_answer}")

    print(f"Вы ответили правильно на {correct_answers} из {len(questions)} вопросов.")
    score_percentage = (correct_answers / len(questions)) * 100
    print(f"Ваш результат: {score_percentage}%")

if __name__ == "__main__":
    quiz()
```

## 3. tic_tac_toe_no_pc.py
```python
board = list(range(1, 10))


def draw_board(board):
    print("-" * 13)
    for i in range(3):
        print("|", board[0 + i * 3], "|", board[1 + i * 3], "|", board[2 + i * 3], "|")
        print("-" * 13)


def take_input(player_token):
    valid = False
    while not valid:
        player_answer = input("Куда поставим " + player_token + "? ")
        try:
            player_answer = int(player_answer)
        except ValueError:
            print("Некорректный ввод. Вы уверены, что ввели число?")
            continue
        if 1 <= player_answer <= 9:
            if str(board[player_answer - 1]) not in "XO":
                board[player_answer - 1] = player_token
                valid = True
            else:
                print("Эта клетка уже занята!")
        else:
            print("Некорректный ввод. Введите число от 1 до 9.")


def check_win(board):
    win_coord = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
    for each in win_coord:
        if board[each[0]] == board[each[1]] == board[each[2]]:
            return board[each[0]]
    return False


def main(board):
    counter = 0
    win = False
    while not win:
        draw_board(board)
        if counter % 2 == 0:
            take_input("X")
        else:
            take_input("O")
        counter += 1

        tmp = check_win(board)
        if tmp:
            print(tmp, "Выиграл!")
            win = True
            break
        if counter == 9:
            print("Ничья!")
            break
    draw_board(board)


if __name__ == '__main__':
    main(board)

input("Нажмите Enter для выхода!")
```

## 4. tic_tac_toe_pc.py
```python
import random

board = list(range(1, 10))


def draw_board(board):
    print("-" * 13)
    for i in range(3):
        print("|", board[0 + i * 3], "|", board[1 + i * 3], "|", board[2 + i * 3], "|")
        print("-" * 13)


def game_pc(): # поменял
    win_coord = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
    for each in win_coord:
        if board[each[0]] == board[each[1]] == 'O' or board[each[0]] == board[each[1]] == 'X':
            return each[2]
        if board[each[0]] == 'O' == board[each[2]] or board[each[0]] == 'X' == board[each[2]]:
            return each[1]
        if 'O' == board[each[1]] == board[each[2]] or 'X' == board[each[1]] == board[each[2]]:
            return each[0]
    while True:
        h = random.randint(0, 8)
        if str(board[h]) not in "XO":
            return h


def take_input(player_token):
    valid = False
    while not valid:
        if player_token == "X":
            player_answer = input("Куда поставим " + player_token + "? ")
        else:
            player_answer = game_pc() # поменял

        try:
            player_answer = int(player_answer)
        except ValueError:
            if player_token == "X":
                print("Некорректный ввод. Вы уверены, что ввели число?")
            continue

        if 1 <= player_answer <= 9:
            if str(board[player_answer - 1]) not in "XO":
                board[player_answer - 1] = player_token
                valid = True
            else:
                if player_token == "X":
                    print("Эта клетка уже занята!")
        else:
            if player_token == "X":
                print("Некорректный ввод. Введите число от 1 до 9.")


def check_win(board):
    win_coord = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
    for each in win_coord:
        if board[each[0]] == board[each[1]] == board[each[2]]:
            return board[each[0]]
    return False


def main(board):
    counter = 0
    win = False
    while not win:
        draw_board(board)
        if counter % 2 == 0:
            take_input("X")
        else:
            print("Ход компьютера...")
            take_input("O")
        counter += 1

        tmp = check_win(board)
        if tmp:
            draw_board(board)
            print(tmp, "Выиграл!")
            win = True
            break
        if counter == 9:
            draw_board(board)
            print("Ничья!")
            break


if __name__ == '__main__':
    main(board)

input("Нажмите Enter для выхода!")
```

## 5. фы.py
```python
import random


def draw_board(board):
    print("\n")
    for row in board:
        print("|".join(row))
        print("-" * 5)


def check_winner(board, player):
    # Проверка строк и столбцов
    for i in range(3):
        if all([cell == player for cell in board[i]]) or all([board[j][i] == player for j in range(3)]):
            return True
    # Проверка диагоналей
    if all([board[i][i] == player for i in range(3)]) or all([board[i][2 - i] == player for i in range(3)]):
        return True
    return False


def get_empty_positions(board):
    return [(i, j) for i in range(3) for j in range(3) if board[i][j] == " "]


def player_move(board):
    while True:
        move = input("Введите ваши координаты (строка и столбец через пробел): ").split()
        if len(move) != 2:
            print("Введите два числа!")
            continue
        row, col = int(move[0]), int(move[1])
        if (row, col) in get_empty_positions(board):
            board[row][col] = "X"
            break
        else:
            print("Эта позиция уже занята или неверна!")


def computer_move(board):
    row, col = random.choice(get_empty_positions(board))
    board[row][col] = "O"


def main():
    board = [[" " for _ in range(3)] for _ in range(3)]
    draw_board(board)

    for _ in range(9):
        player_move(board)
        draw_board(board)
        if check_winner(board, "X"):
            print("Поздравляем! Вы выиграли!")
            return

        if not get_empty_positions(board):
            print("Ничья!")
            return

        computer_move(board)
        draw_board(board)
        if check_winner(board, "O"):
            print("Компьютер выиграл!")
            return

    print("Ничья!")


if __name__ == "__main__":
    main()
```

## 6. new123.py
```python
import pygame
from random import shuffle

pygame.init()
# определяем цвета игры
black = (0, 0, 0)
white = (255, 255, 255)
red = (255, 0, 0)
blue = (0, 0, 255)
green = (0, 255, 0)
yellow = (255, 255, 0)
purple = (128, 0, 128)
grey = (192, 192, 192)

screen_width = 800
screen_height = 600
screen = pygame.display.set_mode((screen_width, screen_height))
pygame.display.set_caption("Тренируем визуальную память")

# задаем параметры окружностей и перемешиваем пары
circle_radius = 50
circle_colors = [red, blue, green, yellow, purple, white]
circle_pairs = circle_colors * 2
shuffle(circle_pairs)

# формируем список окружностей
circle_positions = []
for i in range(6):
    for j in range(2):
        center_x = ((screen_width / 6) * (i + 1)) - (screen_width / 12)
        center_y = ((screen_height / 3) * (j + 1)) - (screen_height / 6)
        circle_positions.append([center_x, center_y])

# запоминаем позиции и цвета окружностей
original_circle_positions = circle_positions.copy()
original_circle_colors = circle_pairs.copy()

# рисуем цветные окружности
for i in range(len(circle_pairs)):
    position = circle_positions[i]
    color = circle_pairs[i]
    pygame.draw.circle(screen, color, position, circle_radius)

font = pygame.font.SysFont('Arial', 20)
pygame.display.update()

# ждем 5 секунд
pygame.time.wait(5000)

# закрываем цветные окружности серыми
for i in range(len(circle_pairs)):
    position = circle_positions[i]
    pygame.draw.circle(screen, grey, position, circle_radius)

pygame.display.update()
uncovered_circles = []
last_uncovered_circle = None
score = 0

while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            exit()

        if event.type == pygame.MOUSEBUTTONDOWN:
            mouse_pos = event.pos
            for i in range(len(circle_positions)):
                position = circle_positions[i]
                if ((position[0] - mouse_pos[0]) ** 2 + (position[1] - mouse_pos[1]) ** 2) ** 0.5 < circle_radius:
                    if i not in uncovered_circles:
                        uncovered_circles.append(i)
                        color = original_circle_colors[i]
                        pygame.draw.circle(screen, color, position, circle_radius)
                        pygame.display.update()
                        if last_uncovered_circle is not None and original_circle_colors[last_uncovered_circle] == \
                                original_circle_colors[i]:
                            score += 1
                        last_uncovered_circle = i

            if len(uncovered_circles) == len(circle_pairs):
                # вывод результата
                final_score_text = font.render(f"Уровень памяти: {str(score)} из 6", True, white)
                screen.blit(final_score_text, (screen_width // 2, screen_height // 2 + 125))
                pygame.display.update()
                pygame.time.wait(3000)
                pygame.quit()
                exit()
```

## 7. б.py
```python
import pygame
import random

# Инициализация Pygame
pygame.init()

# Настройки окна
WIDTH, HEIGHT = 800, 400
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Дино Прыгун")

# Загрузка изображений
dino_img = pygame.image.load("dino.png")  # Добавь свой файл динозавра
cactus_img = pygame.image.load("cactus.png")  # Добавь файл кактуса
dino_img = pygame.transform.scale(dino_img, (50, 50))
cactus_img = pygame.transform.scale(cactus_img, (40, 60))

# Цвета
WHITE = (255, 255, 255)
GROUND_Y = HEIGHT - 50

# Переменные
dino_x = 50
dino_y = GROUND_Y - 50
dino_velocity = 0
gravity = 1
jump_power = -15
on_ground = True

cactus_x = WIDTH
cactus_speed = 5

score = 0
font = pygame.font.Font(None, 36)

# Основной цикл игры
running = True
while running:
    screen.fill(WHITE)

    # Обработка событий
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE and on_ground:
                dino_velocity = jump_power
                on_ground = False

    # Движение динозавра (гравитация)
    dino_y += dino_velocity
    dino_velocity += gravity
    if dino_y >= GROUND_Y - 50:
        dino_y = GROUND_Y - 50
        on_ground = True

    # Движение кактуса
    cactus_x -= cactus_speed
    if cactus_x < -40:
        cactus_x = WIDTH + random.randint(100, 300)
        score += 1  # Очки за прыжок

    # Проверка столкновения
    if dino_x + 50 > cactus_x and dino_x < cactus_x + 40 and dino_y + 50 > GROUND_Y - 60:
        running = False  # Игра окончена

    # Отображение элементов
    screen.blit(dino_img, (dino_x, dino_y))
    screen.blit(cactus_img, (cactus_x, GROUND_Y - 60))

    # Отображение счета
    score_text = font.render(f"Очки: {score}", True, (0, 0, 0))
    screen.blit(score_text, (10, 10))

    pygame.display.update()
    pygame.time.delay(30)

pygame.quit()
```

## 8. eg.py
```python
import pygame

pygame.init()
background = (24, 113, 147)
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 300
game_display = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
pygame.display.set_caption('Колобок')

# загружаем изображение Колобка
kolobok = pygame.image.load('png/kolobok.png')
# стартовый угол вращения и скорость
kolobok_angle = 0
kolobok_rotation_speed = 2

# загружаем фреймы лисы
fox = []
for i in range(8):
    fox.append(pygame.image.load(f'png/fox{i+1}.png'))

# частота обновления фреймов лисы
fox_frame = 0
fox_frame_rate = 8
fox_frame_timer = 0

# стартовые позиции и скорость движения лисы и Колобка
kolobok_x = 0
kolobok_y = WINDOW_HEIGHT // 2 + kolobok.get_height() // 4
fox_x = -fox[0].get_width()
fox_y = WINDOW_HEIGHT // 2 - fox[0].get_height() // 2
movement_speed = 3
clock = pygame.time.Clock()

# главный цикл
game_exit = False
while not game_exit:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            game_exit = True

    # вращаем изображение Колобка вокруг своей оси
    kolobok_angle += kolobok_rotation_speed
    if kolobok_angle >= 360:
        kolobok_angle = 0
    rotated_kolobok = pygame.transform.rotate(kolobok, kolobok_angle)

    # движение Колобка и лисы слева направо
    kolobok_x += movement_speed
    if kolobok_x > WINDOW_WIDTH:
        kolobok_x = 0 - fox[0].get_width()
    fox_x += movement_speed
    if fox_x > WINDOW_WIDTH:
        fox_x = 0 - fox[0].get_width()

    # приводим скорость анимации лисы в соответствие с частотой обновления экрана
    fox_frame_timer += clock.tick(60)
    if fox_frame_timer >= 1000 / fox_frame_rate:
        fox_frame_timer -= 1000 / fox_frame_rate
        fox_frame = (fox_frame + 1) % len(fox)

    # рисуем фон, выводим фигуры Колобка и лисы
    game_display.fill(background)
    game_display.blit(rotated_kolobok, (kolobok_x, kolobok_y))
    game_display.blit(fox[fox_frame], (fox_x, fox_y))
    pygame.display.update()

pygame.quit()
```

## 9. pygme.py (фрагмент с падающими звёздами)
```python
import pygame
import random

pygame.init()

screen_width = 640
screen_height = 480
screen = pygame.display.set_mode((screen_width, screen_height))
pygame.display.set_caption("Звезды падают вниз")

black = (0, 0, 0)
white = (255, 255, 255)
pink = (255, 192, 203)

font = pygame.font.SysFont("Verdana", 15)

star_list = []
for i in range(50):
    x = random.randrange(screen_width)
    y = random.randrange(-200, -50)
    speed = random.randrange(1, 5)
    star_list.append([x, y, speed])
score = 0

freeze = False  # флаг для определения момента остановки

while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            quit()
        if event.type == pygame.MOUSEBUTTONDOWN:  # останавливаем падение звезд по клику
            freeze = True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN: # возобновляем движение вниз, если нажат Enter
            freeze = False

    if not freeze:  # если флаг не активен,
        # звезды падают вниз
        for star in star_list:
            star[1] += star[2]
            if star[1] > screen_height:
                star[0] = random.randrange(screen_width)
                star[1] = random.randrange(-200, -50)
                score += 1

    # рисуем звезды, выводим результаты подсчета
    screen.fill(black)
    for star in star_list:
        pygame.draw.circle(screen, pink, (star[0], star[1]), 3)
    score_text = font.render("Упало звезд: " + str(score), True, white)
    screen.blit(score_text, (10, 10))

    pygame.display.update()

    # устанавливаем частоту обновления экрана
    pygame.time.Clock().tick(60)
```

## 10. main121.py
```python
import pygame
import random

pygame.init()
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
game_display = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
pygame.display.set_caption('Светофор')
clock = pygame.time.Clock()

BLACK = (0, 0, 0)
DARK_GRAY = (64, 64, 64)
GRAY = (128, 128, 128)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)


def game_loop():
    game_exit = False

    # определяем цвета для светофора
    colors = [RED, YELLOW, GREEN]
    active_index = 0
    last_switch = pygame.time.get_ticks()
    interval = 2000

    # параметры автомобилей
    car_width = 40
    car_height = 60
    car_speed = 2
    horizontal_spacing = 12
    vertical_spacing = 20
    car_rects = []
    for i in range(2):
        left_rect = pygame.Rect(100, random.randint(50, WINDOW_HEIGHT - car_height), car_width, car_height)
        right_rect = pygame.Rect(WINDOW_WIDTH - 300 - car_width, random.randint(50, WINDOW_HEIGHT - car_height),
                                 car_width, car_height)
        car_rects.append(left_rect)
        car_rects.append(right_rect)

    # вертикальная и горизонтальная дистанция между автомобилями
    for i in range(1, len(car_rects)):
        if car_rects[i].left - car_rects[i - 1].right < horizontal_spacing:
            car_rects[i].left = car_rects[i - 1].right + horizontal_spacing
        if car_rects[i].top - car_rects[i - 1].bottom < vertical_spacing:
            car_rects[i].top = car_rects[i - 1].bottom + vertical_spacing

    while not game_exit:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                game_exit = True

        # определяем нужный цвет светофора
        now = pygame.time.get_ticks()
        if now - last_switch >= interval:
            active_index = (active_index + 1) % len(colors)
            last_switch = now

        # временной интервал для зеленого цвета - 6 секунд, для остальных - 2
        interval = 6000 if active_index == 2 else 2000

        # движение машин
        if active_index == 0 or active_index == 1:
            car_speed = 0
        else:
            car_speed = 2
        for car_rect in car_rects:
            car_rect.move_ip(0, -car_speed)
            if car_rect.bottom <= 0:
                car_rect.top = WINDOW_HEIGHT
                car_rect.left = 100 if car_rect.left == WINDOW_WIDTH - 100 - car_width else WINDOW_WIDTH - 100 - car_width

        # рисуем светофор
        game_display.fill(GRAY)
        light_rect = pygame.Rect((WINDOW_WIDTH - 200) // 2, (WINDOW_HEIGHT - 300) // 2, 100, 300)
        pygame.draw.rect(game_display, DARK_GRAY, light_rect, 5)
        light_width = light_rect.width
        light_height = light_rect.height // 3
        light_y = light_rect.top
        for i in range(3):
            circle_rect = pygame.Rect(light_rect.left + 10, light_y + i * light_height + 10, light_width - 20,
                                      light_height - 20)
            circle_color = colors[i] if i == active_index else BLACK
            pygame.draw.circle(game_display, circle_color, circle_rect.center, circle_rect.width // 2)

        # рисуем автомобили
        for car_rect in car_rects:
            pygame.draw.rect(game_display, BLUE, car_rect)

        pygame.display.update()
        clock.tick(60)
game_loop()
```

## 11. quiz_pygame.py
```python
import pygame
import sys
import time

# Инициализация Pygame
pygame.init()

# Настройки экрана
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Викторина с таймером")

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)

# Шрифты
font = pygame.font.Font(None, 36)
small_font = pygame.font.Font(None, 28)

# Вопросы и ответы
questions = [
    {
        "question": "Какая столица Франции?",
        "answers": ["Париж", "Лондон", "Берлин", "Мадрид"],
        "correct": 0
    },
    {
        "question": "Сколько планет в Солнечной системе?",
        "answers": ["7", "8", "9", "10"],
        "correct": 1
    },
    {
        "question": "Кто написал 'Гарри Поттера'?",
        "answers": ["Джоан Роулинг", "Стивен Кинг", "Дж. Р. Р. Толкин", "Агата Кристи"],
        "correct": 0
    },{
        "question": "Сколько цветов в радуге?",
        "answers": ["5", "6", "7", "8"],
        "correct": 2
    },
    {
        "question": "Какое животное самое большое на Земле?",
        "answers": ["Слон", "Синий кит", "Жираф", "Бегемот"],
        "correct": 1
    },
    {
        "question": "Как называется спутник Земли?",
        "answers": ["Марс", "Луна", "Венера", "Солнце"],
        "correct": 1
    },
    {
        "question": "Кто написал сказку 'Колобок'?",
        "answers": ["Александр Пушкин", "Русский народ", "Ганс Христиан Андерсен", "Братья Гримм"],
        "correct": 1
    },
    {
        "question": "Какое животное изображено на логотипе Firefox?",
        "answers": ["Лиса", "Медведь", "Панда", "Тигр"],
        "correct": 0
    },
    {
        "question": "Какой инструмент используют, чтобы забивать гвозди?",
        "answers": ["Отвёртка", "Молоток", "Пила", "Клещи"],
        "correct": 1
    },
    {
        "question": "Какой праздник отмечают 31 октября?",
        "answers": ["Новый год", "Хэллоуин", "День рождения", "Рождество"],
        "correct": 1
    },
    {
        "question": "Какое животное говорит 'мяу'?",
        "answers": ["Собака", "Кошка", "Корова", "Лошадь"],
        "correct": 1
    },
    {
        "question": "Какой цвет получится, если смешать красный и синий?",
        "answers": ["Зелёный", "Фиолетовый", "Оранжевый", "Жёлтый"],
        "correct": 1
    },
    {
        "question": "Кто живёт в дупле?",
        "answers": ["Медведь", "Белка", "Заяц", "Лиса"],
        "correct": 1
    },
    {
        "question": "Какой напиток делают из молока?",
        "answers": ["Чай", "Кофе", "Кефир", "Сок"],
        "correct": 2
    },
    {
        "question": "Кто является главным героем сказки 'Буратино'?",
        "answers": ["Чиполлино", "Буратино", "Пиноккио", "Карабас-Барабас"],
        "correct": 1
    },
    {
        "question": "Какой инструмент используют, чтобы рисовать?",
        "answers": ["Кисть", "Молоток", "Ножницы", "Линейка"],
        "correct": 0
    },
    {
        "question": "Какое время года наступает после зимы?",
        "answers": ["Лето", "Осень", "Весна", "Зима"],
        "correct": 2
    },
    {
        "question": "Кто является автором сказки 'Алиса в Стране чудес'?",
        "answers": ["Льюис Кэрролл", "Джоан Роулинг", "Астрид Линдгрен", "Ганс Христиан Андерсен"],
        "correct": 0
    },
    {
        "question": "Какой овощ используют, чтобы сделать морковку?",
        "answers": ["Картофель", "Морковь", "Огурец", "Помидор"],
        "correct": 1
    },
    {
        "question": "Какое животное является символом Австралии?",
        "answers": ["Кенгуру", "Панда", "Лев", "Тигр"],
        "correct": 0
    }
]
# Переменные игры
current_question = 0
score = 0
timer = 10  # Время на ответ (в секундах)
start_time = time.time()

# Функция для отрисовки текста
def draw_text(text, font, color, x, y):
    text_surface = font.render(text, True, color)
    text_rect = text_surface.get_rect(center=(x, y))
    screen.blit(text_surface, text_rect)

# Основной цикл игры
running = True
while running:
    screen.fill(WHITE)

    # Обработка событий
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.MOUSEBUTTONDOWN:
            x, y = event.pos
            # Проверка выбора ответа
            for i, answer in enumerate(questions[current_question]["answers"]):
                if 200 <= x <= 600 and 300 + i * 50 <= y <= 330 + i * 50:
                    if i == questions[current_question]["correct"]:
                        score += 1
                    current_question += 1
                    start_time = time.time()  # Сброс таймера

    # Проверка завершения викторины
    if current_question >= len(questions):
        draw_text("Викторина завершена!", font, BLACK, WIDTH // 2, HEIGHT // 2 - 50)
        draw_text(f"Ваш результат: {score}/{len(questions)}", font, BLACK, WIDTH // 2, HEIGHT // 2)
        pygame.display.flip()
        pygame.time.wait(3000)  # Задержка перед закрытием
        running = False
        continue
    # Отрисовка вопроса
    draw_text(questions[current_question]["question"], font, BLACK, WIDTH // 2, 100)

    # Отрисовка ответов
    for i, answer in enumerate(questions[current_question]["answers"]):
        pygame.draw.rect(screen, BLUE, (200, 300 + i * 50, 400, 30))
        draw_text(answer, small_font, WHITE, WIDTH // 2, 315 + i * 50)

    # Отрисовка таймера
    elapsed_time = time.time() - start_time
    remaining_time = max(0, timer - int(elapsed_time))
    draw_text(f"Осталось времени: {remaining_time} сек", font, BLACK, WIDTH // 2, 200)

    # Проверка времени
    if remaining_time == 0:
        current_question += 1
        start_time = time.time()

    # Обновление экрана
    pygame.display.flip()

# Завершение Pygame
pygame.quit()
sys.exit()
```

## 12. virtual_plant_shapes.py
```python
import pygame
import random

# Инициализация Pygame
pygame.init()

# Настройки окна
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Виртуальный питомец-ботаник")

# Цвета
WHITE = (255, 255, 255)
GREEN = (34, 139, 34)
BROWN = (139, 69, 19)
YELLOW = (255, 215, 0)
BLUE = (135, 206, 250)
DARK_GREEN = (0, 100, 0)
RED = (255, 0, 0)


# Переменные состояния
plant_stage = "семя"
water_level = 50  # Уровень воды (0-100)
light_level = 50  # Уровень света (0-100)
fertilizer = 0    # Количество удобрений
plant_height = 50  # Высота растения
game_over = False  # Флаг окончания игры
game_won = False   # Флаг победы

# Основной цикл игры
running = True
clock = pygame.time.Clock()

while running:
    screen.fill(BLUE)

    # Проверка проигрыша
    if water_level <= 0 or light_level <= 0:
        game_over = True

    # Проверка выигрыша
    if plant_height >= HEIGHT - 50:
        game_won = True

    # Если игра не окончена, отрисовываем растение
    if not game_over and not game_won:
        if plant_stage == "семя":
            pygame.draw.circle(screen, BROWN, (400, HEIGHT - 100), 20)  # Семя - круг
        elif plant_stage == "росток":
            pygame.draw.ellipse(screen, GREEN, (390, HEIGHT - plant_height, 20, 50))  # Росток - овал
        elif plant_stage == "взрослое":
            pygame.draw.rect(screen, DARK_GREEN, (390, HEIGHT - plant_height, 20, plant_height))  # Стебель
            pygame.draw.ellipse(screen, GREEN, (360, HEIGHT - plant_height - 30, 80, 40))  # Крона
        elif plant_stage == "цветение":
            pygame.draw.rect(screen, DARK_GREEN, (390, HEIGHT - plant_height, 20, plant_height))  # Стебель
            pygame.draw.ellipse(screen, GREEN, (360, HEIGHT - plant_height - 30, 80, 40))  # Крона
            pygame.draw.circle(screen, YELLOW, (400, HEIGHT - plant_height - 40), 15)  # Цветок

        # Отображение параметров ухода
        font = pygame.font.Font(None, 36)
        text = font.render(f"Вода: {int(water_level)} | Свет: {int(light_level)} | Удобрения: {fertilizer}", True, WHITE)
        screen.blit(text, (50, 50))

        # Рост растения в зависимости от показателей
        growth_factor = (water_level + light_level) / 200 + (fertilizer * 0.1)  # Коэффициент роста (0 - 1.5)
        plant_height += growth_factor * 0.5  # Медленный рост

        # Обновление стадии растения
        if water_level > 40 and light_level > 40 and fertilizer > 2:
            if plant_stage == "семя":
                plant_stage = "росток"
            elif plant_stage == "росток":
                plant_stage = "взрослое"
            elif plant_stage == "взрослое":
                plant_stage = "цветение"

    # Вывод сообщения о конце игры
    font_large = pygame.font.Font(None, 50)
    if game_over:
        text = font_large.render("Ты проиграл! Растение засохло! 😢", True, RED)
        screen.blit(text, (WIDTH // 4, HEIGHT // 2))
    elif game_won:
        text = font_large.render("Поздравляем! Ты вырастил растение! 🎉", True, GREEN)
        screen.blit(text, (WIDTH // 5, HEIGHT // 2))

    # Проверка событий
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN and not game_over and not game_won:
            if event.key == pygame.K_w:  # Полив
                water_level = min(100, water_level + 10)
            if event.key == pygame.K_l:  # Освещение
                light_level = min(100, light_level + 10)
            if event.key == pygame.K_f:  # Удобрение
                fertilizer += 1

    # Постепенное уменьшение параметров
    if not game_over and not game_won:
        water_level = max(0, water_level - 0.1)
        light_level = max(0, light_level - 0.05)

    pygame.display.flip()
    clock.tick(30)

pygame.quit()
```

## 13. cristmacs.py
```python
import pygame
import random

# Инициализация Pygame
pygame.init()

# Размеры окна
width, height = 800, 600
screen = pygame.display.set_mode((width, height))
pygame.display.set_caption("Новогодняя игра")

# Цвета
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)

# Загружаем изображения
snowflake_image = pygame.Surface((30, 30))
snowflake_image.fill(WHITE)

gift_image = pygame.Surface((50, 50))
gift_image.fill(RED)

# Игрок
player_width, player_height = 50, 50
player = pygame.Rect(width // 2 - player_width // 2, height - player_height - 10, player_width, player_height)

# Скорость игрока
player_speed = 5

# Переменные игры
snowflakes = []
gifts = []
score = 0
game_over = False

# Шрифты
font = pygame.font.Font(None, 36)

# Основной игровой цикл
clock = pygame.time.Clock()

while True:
    screen.fill(BLACK)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            exit()

    # Управление игроком
    keys = pygame.key.get_pressed()
    if keys[pygame.K_LEFT] and player.left > 0:
        player.x -= player_speed
    if keys[pygame.K_RIGHT] and player.right < width:
        player.x += player_speed

    # Добавляем снежинки
    if random.random() < 0.05:
        x = random.randint(0, width - 30)
        snowflakes.append(pygame.Rect(x, 0, 30, 30))

    # Добавляем подарки
    if random.random() < 0.03:
        x = random.randint(0, width - 50)
        gifts.append(pygame.Rect(x, 0, 50, 50))

    # Обновляем снежинки
    for snowflake in snowflakes:
        snowflake.y += 5
        if snowflake.y > height:
            snowflakes.remove(snowflake)
        pygame.draw.rect(screen, WHITE, snowflake)

    # Обновляем подарки
    for gift in gifts:
        gift.y += 3
        if gift.y > height:
            gifts.remove(gift)
        pygame.draw.rect(screen, RED, gift)

    # Проверка на столкновение с подарками
    for gift in gifts:
        if player.colliderect(gift):
            gifts.remove(gift)
            score += 1

    # Отображение игрока
    pygame.draw.rect(screen, GREEN, player)

    # Отображаем счет
    if not game_over:
        score_text = font.render(f"Счет: {score}", True, WHITE)
        screen.blit(score_text, (10, 10))

    # Проверка на игру
    for snowflake in snowflakes:
        if player.colliderect(snowflake):
            game_over = True
    # Если игра закончена
    if game_over:
        game_over_text = font.render("Игра окончена!", True, RED)
        screen.blit(game_over_text, (width // 2 - 100, height // 2))

    pygame.display.flip()
    clock.tick(60)
```

## 14. se.py
```python
import pygame
import random

# Инициализация Pygame
pygame.init()

# Размер окна
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Санта раздает подарки!")

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (135, 206, 235)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BROWN = (139, 69, 19)
GRAY = (128, 128, 128)

# Санта-Клаус
santa = pygame.Rect(WIDTH // 2, HEIGHT // 2, 80, 60)
santa_speed = 7

# Дымоходы домов
houses = [pygame.Rect(random.randint(0, WIDTH - 100), HEIGHT - 100, 100, 100) for _ in range(5)]

# Подарки
gifts = []
gift_speed = 5

# Преграды
obstacles = []
obstacle_speed = 4
obstacle_spawn_time = 50  # Частота появления преград (в кадрах)
obstacle_timer = 0

# Счет
score = 0

# Шрифт
font = pygame.font.Font(None, 36)
game_over_font = pygame.font.Font(None, 72)

# Состояние игры
running = True
game_over = False
blink_timer = 0
blink_state = True

# Основной игровой цикл
clock = pygame.time.Clock()
while True:
    screen.fill(BLUE)

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            exit()
        if not game_over and event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
            # Санта бросает подарок
            gifts.append(pygame.Rect(santa.centerx - 15, santa.centery, 30, 30))

    if not game_over:
        # Управление Санта-Клаусом
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT] and santa.left > 0:
            santa.x -= santa_speed
        if keys[pygame.K_RIGHT] and santa.right < WIDTH:
            santa.x += santa_speed

        # Движение подарков
        gifts_to_remove = []  # Список для удаления подарков
        for gift in gifts:
            gift.y += gift_speed
            if gift.top > HEIGHT:
                gifts_to_remove.append(gift)
            else:
                # Проверка попадания в дымоход
                for house in houses:
                    chimney_rect = pygame.Rect(house.centerx - 10, house.top, 20, 50)
                    if gift.colliderect(chimney_rect):
                        gifts_to_remove.append(gift)
                        score += 1

        for gift in gifts_to_remove:
            if gift in gifts:
                gifts.remove(gift)

        # Движение преград
        if obstacle_timer == 0:
            # Создаем новую преграду
            obstacles.append(pygame.Rect(random.randint(0, WIDTH - 40), 0, 40, 40))
            obstacle_timer = obstacle_spawn_time
        else:
            obstacle_timer -= 1

        for obstacle in obstacles:
            obstacle.y += obstacle_speed
            if obstacle.top > HEIGHT:
                obstacles.remove(obstacle)
            elif obstacle.colliderect(santa):
                game_over = True  # Устанавливаем конец игры

    # Отрисовка домов
    for house in houses:
        pygame.draw.rect(screen, BROWN, house)  # Дом
        chimney_rect = pygame.Rect(house.centerx - 10, house.top, 20, 50)
        pygame.draw.rect(screen, BLACK, chimney_rect)  # Дымоход

    # Отрисовка Санты
    if not game_over or blink_state:
        pygame.draw.rect(screen, RED, santa)

    # Отрисовка подарков
    for gift in gifts:
        pygame.draw.rect(screen, GREEN, gift)

    # Отрисовка преград
    for obstacle in obstacles:
        pygame.draw.rect(screen, GRAY, obstacle)

    # Отображение счета
    if not game_over or blink_state:
        score_text = font.render(f"Счёт: {score}", True, WHITE)
        screen.blit(score_text, (10, 10))

    # Если игра окончена
    if game_over:
        game_over_text = game_over_font.render("Игра окончена", True, WHITE)
        screen.blit(game_over_text, (WIDTH // 2 - 150, HEIGHT // 2 - 50))

        # Управление миганием
        if blink_timer == 0:
            blink_state = not blink_state
            blink_timer = 30  # 2 секунды при 60 FPS
        else:
            blink_timer -= 1

    pygame.display.flip()
    clock.tick(60)
```

## 15. dino.py
```python
import pygame
import random

pygame.init()

WIDTH, HEIGHT = 800, 400
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Дино Прыгун")

# Загрузка изображений динозавра
dino_img = pygame.image.load("dino.png")
dino_img = pygame.transform.scale(dino_img, (50, 50))  # 🔄 Обычный динозавр

dino_duck_img = pygame.image.load("dino_duck.png")  # 🆕 Пригнутый динозавр
dino_duck_img = pygame.transform.scale(dino_duck_img, (50, 30))  # 🆕

# 🔄 Загрузка препятствий
cactus_small = pygame.transform.scale(pygame.image.load("cactus_small.png"), (30, 50))
cactus_double = pygame.transform.scale(pygame.image.load("cactus_double.png"), (70, 60))

bird_img = pygame.transform.scale(pygame.image.load("bird.png"), (50, 40))  # 🔄 Птица

# 🔄 Вариативные препятствия
obstacles = [
    {"img": cactus_small, "width": 30, "height": 50, "type": "cactus"},
    {"img": cactus_double, "width": 70, "height": 60, "type": "cactus"},
    {"img": bird_img, "width": 50, "height": 40, "type": "bird"}
]

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
NIGHT = (20, 20, 40)

GROUND_Y = HEIGHT - 50
font = pygame.font.Font(None, 36)

def draw_text(text, x, y, color=BLACK):
    screen.blit(font.render(text, True, color), (x, y))

def start_menu():
    screen.fill(WHITE)
    draw_text("Дино Прыгун", WIDTH // 2 - 80, HEIGHT // 2 - 50)
    draw_text("Нажми ПРОБЕЛ, чтобы начать", WIDTH // 2 - 150, HEIGHT // 2)
    pygame.display.update()
    waiting = True
    while waiting:
        for event in pygame.event.get():
            if event.type == pygame.QUIT: pygame.quit(); exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                waiting = False

def game_over_menu(score):
    screen.fill(WHITE)
    draw_text("ИГРА ОКОНЧЕНА!", WIDTH // 2 - 100, HEIGHT // 2 - 50)
    draw_text(f"Твой счет: {score}", WIDTH // 2 - 50, HEIGHT // 2)
    draw_text("Нажми ПРОБЕЛ, чтобы сыграть снова", WIDTH // 2 - 200, HEIGHT // 2 + 50)
    pygame.display.update()
    waiting = True
    while waiting:
        for event in pygame.event.get():
            if event.type == pygame.QUIT: pygame.quit(); exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                waiting = False

def game_loop():
    # 🔄 Начальные координаты динозавра
    dino_x = 50
    dino_y = GROUND_Y - 50
    dino_velocity = 0
    gravity = 1
    jump_power = -15
    on_ground = True
    is_ducking = False  # 🆕 Флаг: пригибается ли

    dino_width = 50
    dino_height = 50

    obstacle = random.choice(obstacles)
    obstacle_x = WIDTH

    score = 0
    base_speed = 6
    speed = base_speed

    running = True

    while running:
        # 🔄 День/ночь
        bg_color = NIGHT if (score // 20) % 2 == 1 else WHITE
        text_color = WHITE if bg_color == NIGHT else BLACK
        screen.fill(bg_color)

        for event in pygame.event.get():
            if event.type == pygame.QUIT: pygame.quit(); exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE and on_ground:
                    dino_velocity = jump_power
                    on_ground = False
                if event.key == pygame.K_DOWN:
                    is_ducking = True  # 🆕 Включить пригибание
            if event.type == pygame.KEYUP:
                if event.key == pygame.K_DOWN:
                    is_ducking = False  # 🆕 Отпустил вниз

        # 🔄 Обработка пригибания
        if is_ducking and on_ground:
            dino_height = 30
            dino_img_current = dino_duck_img
            dino_y = GROUND_Y - dino_height
        else:
            dino_height = 50
            dino_img_current = dino_img
            if not on_ground:
                dino_y += dino_velocity
                dino_velocity += gravity
            else:
                dino_y = GROUND_Y - dino_height

        # 🔄 Гравитация
        if dino_y >= GROUND_Y - dino_height:
            dino_y = GROUND_Y - dino_height
            on_ground = True
        else:
            on_ground = False

        # 🔄 Ускорение со временем
        speed = base_speed + score // 10

        # 🔄 Обработка препятствия
        obstacle_x -= speed
        if obstacle_x < -obstacle["width"]:
            obstacle = random.choice(obstacles)
            obstacle_x = WIDTH + random.randint(100, 300)
            score += 1

        # 🔄 Положение птицы
        if obstacle["type"] == "bird":
            obstacle_y = GROUND_Y - 100
        else:
            obstacle_y = GROUND_Y - obstacle["height"]

        # ✅ Столкновение
        dino_rect = pygame.Rect(dino_x, dino_y, dino_width, dino_height)
        obstacle_rect = pygame.Rect(obstacle_x, obstacle_y, obstacle["width"], obstacle["height"])

        # 🆕 Добавим логику: пригнутый динозавр не сталкивается с птицей
        if obstacle["type"] == "bird":
            if dino_rect.colliderect(obstacle_rect):
                if not (is_ducking and on_ground):
                    running = False  # 🆕 Столкновение если НЕ пригнулся
        else:
            if dino_rect.colliderect(obstacle_rect):
                running = False  # 🔄 Кактус — обычная проверка

        # 🔄 Отрисовка
        screen.blit(dino_img_current, (dino_x, dino_y))
        screen.blit(obstacle["img"], (obstacle_x, obstacle_y))
        draw_text(f"Очки: {score}", 10, 10, color=text_color)

        pygame.display.update()
        pygame.time.delay(30)

    game_over_menu(score)

# 🔄 Главный цикл
while True:
    start_menu()
    game_loop()
```

## 16. car.py
```python
import pygame
import random

# Инициализация Pygame
pygame.init()

# Размеры окна
WIDTH, HEIGHT = 400, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Гонки!")

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GRAY = (169, 169, 169)

# Машина игрока
car_width, car_height = 50, 100
player_x = WIDTH // 2 - car_width // 2
player_y = HEIGHT - car_height - 20
player_speed = 8

# Препятствия
obstacle_width, obstacle_height = 50, 100
obstacles = []

# Переменные для сложности
obstacle_speed = 5
obstacle_interval = 1500

# Счёт
score = 0
font = pygame.font.Font(None, 36)


# Функция рисования дороги
def draw_road():
    screen.fill(GRAY)
    pygame.draw.line(screen, WHITE, (WIDTH // 3, 0), (WIDTH // 3, HEIGHT), 5)


# Функция отрисовки игры
def draw_game():
    draw_road()

    # Отрисовка машины игрока
    pygame.draw.rect(screen, BLUE, (player_x, player_y, car_width, car_height))

    # Отрисовка препятствий
    for obstacle in obstacles:
        pygame.draw.rect(screen, RED, (obstacle['x'], obstacle['y'], obstacle_width, obstacle_height))

    # Отрисовка счёта
    score_text = font.render(f"Счёт: {score}", True, BLACK)
    screen.blit(score_text, (10, 10))
    pygame.display.flip()


# Функция обновления препятствий
def update_obstacles():
    global score
    for obstacle in obstacles:
        obstacle['y'] += obstacle_speed

    # Удаление препятствий за пределами экрана и увеличение счёта
    obstacles[:] = [obstacle for obstacle in obstacles if obstacle['y'] <= HEIGHT]
    score += 1
    print(obstacles)
# Функция проверки столкновений
def check_collision():
    for obstacle in obstacles:
        if (player_x < obstacle['x'] + obstacle_width and player_x + car_width > obstacle['x'] and
                player_y < obstacle['y'] + obstacle_height and player_y + car_height > obstacle['y']):
            return True
    return False
# Функция главного меню
def main_menu():
    global obstacle_speed, obstacle_interval

    menu_font = pygame.font.Font(None, 48)
    running = True

    while running:
        screen.fill(WHITE)
        title = menu_font.render("Гонки", True, BLACK)
        easy = font.render("1. Лёгкий (медленно)", True, BLACK)
        medium = font.render("2. Средний (быстрее)", True, BLACK)
        hard = font.render("3. Сложный (очень быстро!)", True, BLACK)

        screen.blit(title, (WIDTH // 2 - title.get_width() // 2, 100))
        screen.blit(easy, (WIDTH // 2 - easy.get_width() // 2, 200))
        screen.blit(medium, (WIDTH // 2 - medium.get_width() // 2, 250))
        screen.blit(hard, (WIDTH // 2 - hard.get_width() // 2, 300))
        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_1:  # Лёгкий уровень
                    obstacle_speed = 5
                    obstacle_interval = 1500
                    running = False
                elif event.key == pygame.K_2:  # Средний уровень
                    obstacle_speed = 7
                    obstacle_interval = 1000
                    running = False
                elif event.key == pygame.K_3:  # Сложный уровень
                    obstacle_speed = 10
                    obstacle_interval = 700
                    running = False


# Главный игровой цикл
def game_loop():
    global score, player_x, obstacles
    score = 0
    player_x = WIDTH // 2 - car_width // 2
    obstacles = []

    # Таймер для появления препятствий
    obstacle_timer = pygame.USEREVENT + 1
    pygame.time.set_timer(obstacle_timer, obstacle_interval)

    running = True
    clock = pygame.time.Clock()

    while running:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            # Добавление новых препятствий
            if event.type == obstacle_timer:
                lane = random.choice([WIDTH // 6 - obstacle_width // 2, WIDTH // 2 - obstacle_width // 2,
                                      WIDTH * 5 // 6 - obstacle_width // 2])
                obstacles.append({'x': lane, 'y': -obstacle_height})

        # Управление машиной
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT] and player_x > 0:
            player_x -= player_speed
        if keys[pygame.K_RIGHT] and player_x < WIDTH - car_width:
            player_x += player_speed

        # Обновление препятствий
        update_obstacles()

        # Проверка на столкновение
        if check_collision():
            game_over_text = font.render("Игра окончена!", True, RED)
            screen.blit(game_over_text, (WIDTH // 2 - game_over_text.get_width() // 2, HEIGHT // 2))
            pygame.display.flip()
            pygame.time.wait(2000)
            running = False

        # Отрисовка игры
        draw_game()


# Запуск игры
while True:
    main_menu()
    game_loop()
```

## 17. sea_fight.py
```python
import pygame
import random
import time

# Инициализация Pygame
pygame.init()

# Параметры окна
WIDTH, HEIGHT = 550, 350
CELL_SIZE = 25  # Размер клетки
GRID_SIZE = 10  # Размер сетки

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 100, 255)
RED = (255, 0, 0)
GRAY = (100, 100, 100)
DARK_GRAY = (50, 50, 50)
GREEN = (0, 255, 0)

# Создание окна
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Морской бой")

# Поля (0 - вода, 1+ - корабли, 2 - промах, 3 - попадание, 4 - затопленный корабль)
player_grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
ai_grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
ai_mask = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]  # Маска для скрытия кораблей

# AI-логика
ai_difficulty = "легкий"

# Счёт
player_score = 0
ai_score = 0


# Расстановка кораблей
def place_ships(grid):
    ships = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]
    for ship in ships:
        placed = False
        while not placed:
            x = random.randint(0, GRID_SIZE - 1)
            y = random.randint(0, GRID_SIZE - 1)
            direction = random.choice([(1, 0), (0, 1)])
            valid = True

            for i in range(ship):
                nx = x + i * direction[0]
                ny = y + i * direction[1]
                if nx >= GRID_SIZE or ny >= GRID_SIZE or grid[nx][ny] != 0:
                    valid = False
                    break

            if valid:
                for i in range(ship):
                    nx = x + i * direction[0]
                    ny = y + i * direction[1]
                    grid[nx][ny] = ship
                placed = True


place_ships(player_grid)
place_ships(ai_grid)


# Проверка затопления корабля
def check_sunken(grid, x, y):
    length = grid[x][y]
    coords = [(x, y)]

    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        i = 1
        while 0 <= x + i * dx < GRID_SIZE and 0 <= y + i * dy < GRID_SIZE:
            if grid[x + i * dx][y + i * dy] == 3:
                coords.append((x + i * dx, y + i * dy))
            else:
                break
            i += 1

    if len(coords) == length:
        for cx, cy in coords:
            grid[cx][cy] = 4
        return True
    return False


# AI ход
def ai_move():
    global ai_score
    while True:
        x = random.randint(0, GRID_SIZE - 1)
        y = random.randint(0, GRID_SIZE - 1)
        if player_grid[x][y] in [0, 1]:
            if player_grid[x][y] == 1:
                player_grid[x][y] = 3
                if check_sunken(player_grid, x, y):
                    ai_score += 1
            else:
                player_grid[x][y] = 2
            return


# Отрисовка сетки
def draw_grid():
    for i in range(GRID_SIZE):
        for j in range(GRID_SIZE):
            # Поле игрока (слева)
            color = BLUE
            if player_grid[i][j] > 0:
                color = WHITE
            if player_grid[i][j] == 3:
                color = RED
            if player_grid[i][j] == 4:
                color = GREEN
            pygame.draw.rect(screen, color, (30 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 0)
            pygame.draw.rect(screen, BLACK, (30 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

            # Поле AI (справа)
            color = BLUE
            if ai_grid[i][j] in [2, 3, 4]:  # Только если была атака
                color = BLACK if ai_grid[i][j] == 2 else RED
                if ai_grid[i][j] == 4:
                    color = GREEN
                ai_mask[i][j] = 1  # Открываем клетку после попадания
            pygame.draw.rect(screen, color, (300 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 0)
            pygame.draw.rect(screen, BLACK, (300 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

    # Разграничивающая линия
    pygame.draw.line(screen, DARK_GRAY, (280, 30), (280, 30 + GRID_SIZE * CELL_SIZE), 3)

    # Счётчик
    font = pygame.font.Font(None, 24)
    text = font.render(f"Вы: {player_score}  |  AI: {ai_score}", True, WHITE)
    screen.blit(text, (200, 5))


# Экран завершения игры
def show_end_screen(winner):
    screen.fill(BLACK)
    font = pygame.font.Font(None, 50)
    text = font.render(f"{winner} победил!", True, WHITE)
    screen.blit(text, (150, 150))
    pygame.display.flip()
    time.sleep(3)


# Меню
def show_menu():
    global ai_difficulty
    menu_running = True
    font = pygame.font.Font(None, 30)

    while menu_running:
        screen.fill(GRAY)
        text1 = font.render("Выберите сложность:", True, WHITE)
        text2 = font.render("1 - Легкий", True, WHITE)
        text3 = font.render("2 - Сложный", True, WHITE)

        screen.blit(text1, (160, 100))
        screen.blit(text2, (200, 150))
        screen.blit(text3, (200, 180))

        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_1:
                    ai_difficulty = "легкий"
                    menu_running = False
                elif event.key == pygame.K_2:
                    ai_difficulty = "сложный"
                    menu_running = False


show_menu()

# Игровой цикл
running = True
while running:
    screen.fill(GRAY)
    draw_grid()

    if player_score >= 10:
        show_end_screen("Игрок")
        break
    elif ai_score >= 10:
        show_end_screen("AI")
        break

    pygame.display.flip()
```

## 18. X0X.py
```python
import pygame
import sys

# Инициализация Pygame
pygame.init()

# Размеры окна
WIDTH, HEIGHT = 600, 600
LINE_WIDTH = 15
WIN_LINE_WIDTH = 15
BOARD_ROWS, BOARD_COLS = 3, 3
SQUARE_SIZE = WIDTH // BOARD_COLS

# Цвета
BG_COLOR = (28, 170, 156)
LINE_COLOR = (23, 145, 135)
CIRCLE_COLOR = (239, 231, 200)
CIRCLE_RADIUS = SQUARE_SIZE // 3
CIRCLE_WIDTH = 15
CROSS_COLOR = (66, 66, 66)
CROSS_WIDTH = 25
CROSS_SPACE = SQUARE_SIZE // 4

# Кнопки
BUTTON_WIDTH, BUTTON_HEIGHT = 200, 50
BUTTON_COLOR = (100, 100, 100)
BUTTON_TEXT_COLOR = (255, 255, 255)
BUTTON_FONT = pygame.font.SysFont(None, 40)

# Настройка экрана
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption('Крестики-Нолики')
screen.fill(BG_COLOR)

# Шрифт
font = pygame.font.SysFont(None, 55)
end_font = pygame.font.SysFont(None, 40)


def draw_board():
    screen.fill(BG_COLOR)
    # Вертикальные линии
    for col in range(1, BOARD_COLS):
        pygame.draw.line(screen, LINE_COLOR, (col * SQUARE_SIZE, 0), (col * SQUARE_SIZE, HEIGHT), LINE_WIDTH)
    # Горизонтальные линии
    for row in range(1, BOARD_ROWS):
        pygame.draw.line(screen, LINE_COLOR, (0, row * SQUARE_SIZE), (WIDTH, row * SQUARE_SIZE), LINE_WIDTH)


def draw_marks(board):
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            if board[row][col] == 'X':
                draw_x(row, col)
            elif board[row][col] == 'O':
                draw_o(row, col)


def draw_x(row, col):
    pygame.draw.line(screen, CROSS_COLOR,
                     (col * SQUARE_SIZE + CROSS_SPACE, row * SQUARE_SIZE + SQUARE_SIZE - CROSS_SPACE),
                     (col * SQUARE_SIZE + SQUARE_SIZE - CROSS_SPACE, row * SQUARE_SIZE + CROSS_SPACE), CROSS_WIDTH)
    pygame.draw.line(screen, CROSS_COLOR, (col * SQUARE_SIZE + CROSS_SPACE, row * SQUARE_SIZE + CROSS_SPACE),
                     (col * SQUARE_SIZE + SQUARE_SIZE - CROSS_SPACE, row * SQUARE_SIZE + SQUARE_SIZE - CROSS_SPACE),
                     CROSS_WIDTH)


def draw_o(row, col):
    pygame.draw.circle(screen, CIRCLE_COLOR,
                       (col * SQUARE_SIZE + SQUARE_SIZE // 2, row * SQUARE_SIZE + SQUARE_SIZE // 2), CIRCLE_RADIUS,
                       CIRCLE_WIDTH)


def check_winner(board):
    # Проверка строк
    for row in range(BOARD_ROWS):
        if board[row][0] == board[row][1] == board[row][2] != None:
            return board[row][0]
    # Проверка столбцов
    for col in range(BOARD_COLS):
        if board[0][col] == board[1][col] == board[2][col] != None:
            return board[0][col]
    # Проверка диагоналей
    if board[0][0] == board[1][1] == board[2][2] != None:
        return board[0][0]
    if board[0][2] == board[1][1] == board[2][0] != None:
        return board[0][2]
    return None


def is_board_full(board):
    for row in board:
        for item in row:
            if item is None:
                return False
    return True


def get_available_moves(board):
    return [(row, col) for row in range(BOARD_ROWS) for col in range(BOARD_COLS) if board[row][col] is None]
def computer_move(board):
    # 1. Проверка на выигрышный ход
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            if board[row][col] is None:
                board[row][col] = 'O'
                if check_winner(board) == 'O':
                    return
                board[row][col] = None
    # 2. Проверка на блокировку хода игрока
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            if board[row][col] is None:
                board[row][col] = 'X'
                if check_winner(board) == 'X':
                    board[row][col] = 'O'
                    return
                board[row][col] = None
    # 3. Занять центр, если он свободен
    if board[1][1] is None:
        board[1][1] = 'O'
        return
    # 4. Занять любой свободный угол
    for row, col in [(0, 0), (0, 2), (2, 0), (2, 2)]:
        if board[row][col] is None:
            board[row][col] = 'O'
            return
    # 5. Занять любую свободную клетку
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            if board[row][col] is None:
                board[row][col] = 'O'
                return


def draw_buttons():
    # Кнопка для игры с компьютером
    pygame.draw.rect(screen, BUTTON_COLOR,
                     (WIDTH // 2 - BUTTON_WIDTH // 2, HEIGHT // 2 - BUTTON_HEIGHT - 10, BUTTON_WIDTH, BUTTON_HEIGHT))
    text = BUTTON_FONT.render('Играть с компьютером', True, BUTTON_TEXT_COLOR)
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT // 2 - BUTTON_HEIGHT // 2 - 10))
    # Кнопка для игры с другом
    pygame.draw.rect(screen, BUTTON_COLOR,
                     (WIDTH // 2 - BUTTON_WIDTH // 2, HEIGHT // 2 + 10, BUTTON_WIDTH, BUTTON_HEIGHT))
    text = BUTTON_FONT.render('Играть с другом', True, BUTTON_TEXT_COLOR)
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT // 2 + BUTTON_HEIGHT // 2 + 10))


def draw_end_menu(winner):
    if winner == 'X':
        end_text = "Вы выиграли!"
    elif winner == 'O':
        end_text = "Вы проиграли!"
    else:
        end_text = "Ничья!"
    text = end_font.render(end_text, True, (255, 255, 255))
    pygame.draw.rect(screen, (0, 0, 0), (WIDTH // 2 - 150, HEIGHT // 2 - 50, 300, 100))
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT // 2 - text.get_height() // 2))


def main():
    board = [[None for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)]
    current_player = 'X'
    game_over = False
    waiting_for_choice = True
    choice = None

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if waiting_for_choice:
                if event.type == pygame.MOUSEBUTTONDOWN:
                    mouse_x, mouse_y = pygame.mouse.get_pos()
                    # Проверка нажатия на кнопку "Играть с компьютером"
                    if WIDTH // 2 - BUTTON_WIDTH // 2 <= mouse_x <= WIDTH // 2 + BUTTON_WIDTH // 2 \
                            and HEIGHT // 2 - BUTTON_HEIGHT - 10 <= mouse_y <= HEIGHT // 2 - 10:
                        choice = 'computer'
                        waiting_for_choice = False
                    # Проверка нажатия на кнопку "Играть с другом"
                    if WIDTH // 2 - BUTTON_WIDTH // 2 <= mouse_x <= WIDTH // 2 + BUTTON_WIDTH // 2 \
                            and HEIGHT // 2 + 10 <= mouse_y <= HEIGHT // 2 + BUTTON_HEIGHT + 10:
                        choice = 'friend'
                        waiting_for_choice = False
            else:
                if not game_over:
                    if choice == 'computer' and current_player == 'O':
                        computer_move(board)
                        current_player = 'X'
                    elif choice == 'friend':
                        if event.type == pygame.MOUSEBUTTONDOWN:
                            mouse_x, mouse_y = pygame.mouse.get_pos()
                            col, row = mouse_x // SQUARE_SIZE, mouse_y // SQUARE_SIZE
                            if board[row][col] is None:
                                board[row][col] = current_player
                                current_player = 'O' if current_player == 'X' else 'X'
                    else:
                        if event.type == pygame.MOUSEBUTTONDOWN:
                            mouse_x, mouse_y = pygame.mouse.get_pos()
                            col, row = mouse_x // SQUARE_SIZE, mouse_y // SQUARE_SIZE
                            if board[row][col] is None:
                                board[row][col] = current_player
                                current_player = 'O' if current_player == 'X' else 'X'
                else:
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        mouse_x, mouse_y = pygame.mouse.get_pos()
                        # Перезапуск игры
                        board = [[None for _ in range(BOARD_COLS)] for _ in range(BOARD_ROWS)]
                        current_player = 'X'
                        game_over = False
                        waiting_for_choice = True

        if not waiting_for_choice:
            draw_board()
            draw_marks(board)
            winner = check_winner(board)
            if winner:
                game_over = True
                draw_end_menu(winner)
            elif is_board_full(board):
                game_over = True
                draw_end_menu(None)
        else:
            draw_buttons()

        pygame.display.update()


if __name__ == "__main__":
    main()
```

## 19. new_sea_fight.py
```python
import pygame
import random
import time
import sys

# Инициализация Pygame
pygame.init()

# Параметры окна
WIDTH, HEIGHT = 550, 350
CELL_SIZE = 25  # Размер клетки
GRID_SIZE = 10  # Размер сетки

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 100, 255)
RED = (255, 0, 0)
GRAY = (100, 100, 100)
DARK_GRAY = (50, 50, 50)
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)

# Создание окна
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Морской бой")

# Поля (0 - вода, 1+ - корабли, 2 - промах, 3 - попадание, 4 - затопленный корабль)
player_grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]
ai_grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]

# AI-логика
ai_difficulty = "легкий"

# Счёт
player_score = 0
ai_score = 0

# Состояние игры
last_hit = None
message_timer = 0


def create_empty_grid():
    return [[0] * GRID_SIZE for _ in range(GRID_SIZE)]


def place_ships(grid):
    ships = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]  # Стандартный набор кораблей
    new_grid = create_empty_grid()  # Создаем новую пустую сетку

    for ship in ships:
        placed = False
        attempts = 0
        while not placed and attempts < 1000:  # Ограничение попыток
            attempts += 1
            x = random.randint(0, GRID_SIZE - 1)
            y = random.randint(0, GRID_SIZE - 1)
            direction = random.choice([(1, 0), (0, 1)])

            # Проверка, поместится ли корабль
            if (x + (ship - 1) * direction[0] >= GRID_SIZE or
                    y + (ship - 1) * direction[1] >= GRID_SIZE):
                continue

            # Проверка возможности размещения
            valid = True
            for i in range(-1, ship + 1):
                for j in range(-1, 2):
                    check_x = x + (i * direction[0]) + (j * direction[1] if direction[0] == 1 else 0)
                    check_y = y + (i * direction[1]) + (j * direction[0] if direction[1] == 1 else 0)

                    if (check_x >= 0 and check_x < GRID_SIZE and
                            check_y >= 0 and check_y < GRID_SIZE and
                            new_grid[check_x][check_y] != 0):
                        valid = False
                        break
                if not valid:
                    break

            if valid:
                # Размещаем корабль
                for i in range(ship):
                    nx = x + i * direction[0]
                    ny = y + i * direction[1]
                    new_grid[nx][ny] = 1  # Всегда используем 1 для обозначения корабля
                placed = True

        if not placed:
            # Если не удалось разместить корабль, начинаем заново
            return place_ships(grid)

    return new_grid


def check_sunken(grid, x, y):
    ship_type = grid[x][y]
    ship_cells = [(x, y)]
    checked = set([(x, y)])
    to_check = [(x, y)]

    while to_check:
        cx, cy = to_check.pop(0)
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nx, ny = cx + dx, cy + dy
            if (0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and
                    (nx, ny) not in checked and
                    grid[nx][ny] in [ship_type, 3, 4]):
                checked.add((nx, ny))
                ship_cells.append((nx, ny))
                to_check.append((nx, ny))

    all_hit = all(grid[cx][cy] in [3, 4] for cx, cy in ship_cells)
    if all_hit:
        for cx, cy in ship_cells:
            grid[cx][cy] = 4
        return True
    return False


def ai_move():
    global ai_score, last_hit

    # Поиск доступных клеток
    available_cells = [
        (x, y) for x in range(GRID_SIZE)
        for y in range(GRID_SIZE)
        if player_grid[x][y] in [0, 1]
    ]

    if not available_cells:
        return False

    # Сложный режим
    if ai_difficulty == "сложный" and random.random() < 0.8:
        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                if player_grid[x][y] == 3:
                    for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        nx, ny = x + dx, y + dy
                        if (0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and
                                player_grid[nx][ny] in [0, 1]):
                            if player_grid[nx][ny] == 1:
                                player_grid[nx][ny] = 3
                                last_hit = "ai_hit"
                                if check_sunken(player_grid, nx, ny):
                                    ai_score += 1
                                    last_hit = "ai_sunk"
                            else:
                                player_grid[nx][ny] = 2
                                last_hit = "ai_miss"
                            return True

    # Случайный выстрел
    x, y = random.choice(available_cells)
    if player_grid[x][y] == 1:
        player_grid[x][y] = 3
        last_hit = "ai_hit"
        if check_sunken(player_grid, x, y):
            ai_score += 1
            last_hit = "ai_sunk"
    else:
        player_grid[x][y] = 2
        last_hit = "ai_miss"
    return True


def draw_grid():
    for i in range(GRID_SIZE):
        for j in range(GRID_SIZE):
            # Поле игрока
            color = BLUE
            if player_grid[i][j] > 0 and player_grid[i][j] < 2:
                color = WHITE
            if player_grid[i][j] == 2:
                color = BLACK
            if player_grid[i][j] == 3:
                color = RED
            if player_grid[i][j] == 4:
                color = GREEN

            pygame.draw.rect(screen, color,
                             (30 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 0)
            pygame.draw.rect(screen, BLACK,
                             (30 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

            # Поле компьютера - показываем только выстрелы
            color = BLUE
            if ai_grid[i][j] == 2:  # Промах
                color = BLACK
            elif ai_grid[i][j] == 3:  # Попадание
                color = RED
            elif ai_grid[i][j] == 4:  # Потопленный корабль
                color = GREEN
            # Не показываем нетронутые корабли (значение 1)

            pygame.draw.rect(screen, color,
                             (300 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 0)
            pygame.draw.rect(screen, BLACK,
                             (300 + i * CELL_SIZE, 30 + j * CELL_SIZE, CELL_SIZE, CELL_SIZE), 1)

    # Разделитель и подписи
    pygame.draw.line(screen, DARK_GRAY, (280, 30), (280, 30 + GRID_SIZE * CELL_SIZE), 3)

    font = pygame.font.Font(None, 24)
    screen.blit(font.render("Ваше поле", True, WHITE), (90, 10))
    screen.blit(font.render("Поле противника", True, WHITE), (330, 10))
    screen.blit(font.render(f"Счет: {player_score} - {ai_score}", True, WHITE), (230, 300))


def draw_messages():
    global message_timer
    if last_hit and message_timer > 0:
        font = pygame.font.Font(None, 30)
        messages = {
            "player_hit": ("Вы попали!", RED),
            "player_miss": ("Вы промахнулись", BLACK),
            "player_sunk": ("Корабль потоплен!", GREEN),
            "ai_hit": ("Компьютер попал", RED),
            "ai_miss": ("Компьютер промахнулся", BLACK),
            "ai_sunk": ("Компьютер потопил корабль", GREEN)
        }

        if last_hit in messages:
            message, color = messages[last_hit]
            text = font.render(message, True, color)
            screen.blit(text, (WIDTH // 2 - text.get_width() // 2, 280))
        message_timer -= 1


def show_end_screen(winner):
    screen.fill(BLACK)
    font = pygame.font.Font(None, 50)
    text = font.render(f"{'Вы победили!' if winner == 'Игрок' else 'Компьютер победил'}",
                       True, GREEN if winner == 'Игрок' else RED)
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT // 2))
    pygame.display.flip()

    waiting = True
    start_time = time.time()
    while waiting and time.time() - start_time < 3:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                waiting = False
            if event.type == pygame.KEYDOWN:
                waiting = False


def show_menu():
    global ai_difficulty
    menu_running = True
    selected = 0

    while menu_running:
        screen.fill(DARK_GRAY)
        font_big = pygame.font.Font(None, 50)
        font_small = pygame.font.Font(None, 30)

        title = font_big.render("МОРСКОЙ БОЙ", True, WHITE)
        screen.blit(title, (WIDTH // 2 - title.get_width() // 2, 50))

        options = ["Легкий", "Сложный"]
        for i, option in enumerate(options):
            color = YELLOW if i == selected else WHITE
            text = font_small.render(option, True, color)
            screen.blit(text, (WIDTH // 2 - text.get_width() // 2, 180 + i * 40))

        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key in [pygame.K_UP, pygame.K_DOWN]:
                    selected = 1 - selected
                elif event.key == pygame.K_RETURN:
                    ai_difficulty = "легкий" if selected == 0 else "сложный"
                    return True
                elif event.key in [pygame.K_1, pygame.K_2]:
                    ai_difficulty = "легкий" if event.key == pygame.K_1 else "сложный"
                    return True
    return False


def main():
    global player_grid, ai_grid, player_score, ai_score, player_turn, last_hit, message_timer

    if not show_menu():
        return

    # Инициализация игры
    player_grid = create_empty_grid()
    ai_grid = create_empty_grid()
    
    # Размещаем корабли
    player_grid = place_ships(player_grid)
    ai_grid = place_ships(ai_grid)
    
    # Сбрасываем все счетчики
    player_score = 0
    ai_score = 0
    player_turn = True
    last_hit = None
    message_timer = 0

    clock = pygame.time.Clock()
    running = True

    while running:
        screen.fill(GRAY)
        draw_grid()
        draw_messages()

        font = pygame.font.Font(None, 24)
        turn_text = font.render("Ваш ход" if player_turn else "Ход компьютера", True, WHITE)
        screen.blit(turn_text, (230, 250))

        pygame.display.flip()

        if player_score >= 10:
            show_end_screen("Игрок")
            running = False
        elif ai_score >= 10:
            show_end_screen("AI")
            running = False

        if not player_turn:
            time.sleep(0.7)
            if ai_move():
                message_timer = 90
                player_turn = True

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.MOUSEBUTTONDOWN and player_turn:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                if 300 <= mouse_x < 300 + GRID_SIZE * CELL_SIZE and 30 <= mouse_y < 30 + GRID_SIZE * CELL_SIZE:
                    grid_x = (mouse_x - 300) // CELL_SIZE
                    grid_y = (mouse_y - 30) // CELL_SIZE

                    if ai_grid[grid_x][grid_y] in [0, 1]:
                        if ai_grid[grid_x][grid_y] == 1:
                            ai_grid[grid_x][grid_y] = 3
                            last_hit = "player_hit"
                            if check_sunken(ai_grid, grid_x, grid_y):
                                player_score += 1
                                last_hit = "player_sunk"
                        else:
                            ai_grid[grid_x][grid_y] = 2
                            last_hit = "player_miss"

                        message_timer = 90
                        player_turn = False

        clock.tick(60)


if __name__ == "__main__":
    try:
        main()
    finally:
        pygame.quit()
        sys.exit()
```

## 20. tetris.py
```python
import pygame as pg
import random, time, sys
from pygame.locals import *

fps = 25
window_w, window_h = 600, 500
block, cup_h, cup_w = 20, 20, 10

side_freq, down_freq = 0.15, 0.1  # передвижение в сторону и вниз

side_margin = int((window_w - cup_w * block) / 2)
top_margin = window_h - (cup_h * block) - 5

colors = ((0, 0, 225), (0, 225, 0), (225, 0, 0), (225, 225, 0))  # синий, зеленый, красный, желтый
lightcolors = ((30, 30, 255), (50, 255, 50), (255, 30, 30),
               (255, 255, 30))  # светло-синий, светло-зеленый, светло-красный, светло-желтый

white, gray, black = (255, 255, 255), (185, 185, 185), (0, 0, 0)
brd_color, bg_color, txt_color, title_color, info_color = white, black, white, colors[3], colors[0]

fig_w, fig_h = 5, 5
empty = 'o'

figures = {'S': [['ooooo',
                  'ooooo',
                  'ooxxo',
                  'oxxoo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'ooxxo',
                  'oooxo',
                  'ooooo']],
           'Z': [['ooooo',
                  'ooooo',
                  'oxxoo',
                  'ooxxo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'oxxoo',
                  'oxooo',
                  'ooooo']],
           'J': [['ooooo',
                  'oxooo',
                  'oxxxo',
                  'ooooo',
                  'ooooo'],
                 ['ooooo',
                  'ooxxo',
                  'ooxoo',
                  'ooxoo',
                  'ooooo'],
                 ['ooooo',
                  'ooooo',
                  'oxxxo',
                  'oooxo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'ooxoo',
                  'oxxoo',
                  'ooooo']],
           'L': [['ooooo',
                  'oooxo',
                  'oxxxo',
                  'ooooo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'ooxoo',
                  'ooxxo',
                  'ooooo'],
                 ['ooooo',
                  'ooooo',
                  'oxxxo',
                  'oxooo',
                  'ooooo'],
                 ['ooooo',
                  'oxxoo',
                  'ooxoo',
                  'ooxoo',
                  'ooooo']],
           'I': [['ooxoo',
                  'ooxoo',
                  'ooxoo',
                  'ooxoo',
                  'ooooo'],
                 ['ooooo',
                  'ooooo',
                  'xxxxo',
                  'ooooo',
                  'ooooo']],
           'O': [['ooooo',
                  'ooooo',
                  'oxxoo',
                  'oxxoo',
                  'ooooo']],
           'T': [['ooooo',
                  'ooxoo',
                  'oxxxo',
                  'ooooo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'ooxxo',
                  'ooxoo',
                  'ooooo'],
                 ['ooooo',
                  'ooooo',
                  'oxxxo',
                  'ooxoo',
                  'ooooo'],
                 ['ooooo',
                  'ooxoo',
                  'oxxoo',
                  'ooxoo',
                  'ooooo']]}


def pauseScreen():
    pause = pg.Surface((600, 500), pg.SRCALPHA)
    pause.fill((0, 0, 255, 127))
    display_surf.blit(pause, (0, 0))


def main():
    global fps_clock, display_surf, basic_font, big_font
    pg.init()
    fps_clock = pg.time.Clock()
    display_surf = pg.display.set_mode((window_w, window_h))
    basic_font = pg.font.SysFont('arial', 20)
    big_font = pg.font.SysFont('verdana', 45)
    pg.display.set_caption('Тетрис Lite')
    showText('Тетрис Lite')
    while True:  # начинаем игру
        runTetris()
        pauseScreen()
        showText('Игра закончена')


def runTetris():
    cup = emptycup()
    last_move_down = time.time()
    last_side_move = time.time()
    last_fall = time.time()
    going_down = False
    going_left = False
    going_right = False
    points = 0
    level, fall_speed = calcSpeed(points)
    fallingFig = getNewFig()
    nextFig = getNewFig()

    while True:
        if fallingFig == None:
            # если нет падающих фигур, генерируем новую
            fallingFig = nextFig
            nextFig = getNewFig()
            last_fall = time.time()

            if not checkPos(cup, fallingFig):
                return  # если на игровом поле нет свободного места - игра закончена
        quitGame()
        for event in pg.event.get():
            if event.type == KEYUP:
                if event.key == K_SPACE:
                    pauseScreen()
                    showText('Пауза')
                    last_fall = time.time()
                    last_move_down = time.time()
                    last_side_move = time.time()
                elif event.key == K_LEFT:
                    going_left = False
                elif event.key == K_RIGHT:
                    going_right = False
                elif event.key == K_DOWN:
                    going_down = False

            elif event.type == KEYDOWN:
                # перемещение фигуры вправо и влево
                if event.key == K_LEFT and checkPos(cup, fallingFig, adjX=-1):
                    fallingFig['x'] -= 1
                    going_left = True
                    going_right = False
                    last_side_move = time.time()

                elif event.key == K_RIGHT and checkPos(cup, fallingFig, adjX=1):
                    fallingFig['x'] += 1
                    going_right = True
                    going_left = False
                    last_side_move = time.time()

                # поворачиваем фигуру, если есть место
                elif event.key == K_UP:
                    fallingFig['rotation'] = (fallingFig['rotation'] + 1) % len(figures[fallingFig['shape']])
                    if not checkPos(cup, fallingFig):
                        fallingFig['rotation'] = (fallingFig['rotation'] - 1) % len(figures[fallingFig['shape']])

                # ускоряем падение фигуры
                elif event.key == K_DOWN:
                    going_down = True
                    if checkPos(cup, fallingFig, adjY=1):
                        fallingFig['y'] += 1
                    last_move_down = time.time()

                # мгновенный сброс вниз
                elif event.key == K_RETURN:
                    going_down = False
                    going_left = False
                    going_right = False
                    for i in range(1, cup_h):
                        if not checkPos(cup, fallingFig, adjY=i):
                            break
                    fallingFig['y'] += i - 1

        # управление падением фигуры при удержании клавиш
        if (going_left or going_right) and time.time() - last_side_move > side_freq:
            if going_left and checkPos(cup, fallingFig, adjX=-1):
                fallingFig['x'] -= 1
            elif going_right and checkPos(cup, fallingFig, adjX=1):
                fallingFig['x'] += 1
            last_side_move = time.time()

        if going_down and time.time() - last_move_down > down_freq and checkPos(cup, fallingFig, adjY=1):
            fallingFig['y'] += 1
            last_move_down = time.time()

        if time.time() - last_fall > fall_speed:  # свободное падение фигуры
            if not checkPos(cup, fallingFig, adjY=1):  # проверка "приземления" фигуры
                addToCup(cup, fallingFig)  # фигура приземлилась, добавляем ее в содержимое стакана
                points += clearCompleted(cup)
                level, fall_speed = calcSpeed(points)
                fallingFig = None
            else:  # фигура пока не приземлилась, продолжаем движение вниз
                fallingFig['y'] += 1
                last_fall = time.time()

        # рисуем окно игры со всеми надписями
        display_surf.fill(bg_color)
        drawTitle()
        gamecup(cup)
        drawInfo(points, level)
        drawnextFig(nextFig)
        if fallingFig != None:
            drawFig(fallingFig)
        pg.display.update()
        fps_clock.tick(fps)


def txtObjects(text, font, color):
    surf = font.render(text, True, color)
    return surf, surf.get_rect()


def stopGame():
    pg.quit()
    sys.exit()


def checkKeys():
    quitGame()

    for event in pg.event.get([KEYDOWN, KEYUP]):
        if event.type == KEYDOWN:
            continue
        return event.key
    return None


def showText(text):
    titleSurf, titleRect = txtObjects(text, big_font, title_color)
    titleRect.center = (int(window_w / 2) - 3, int(window_h / 2) - 3)
    display_surf.blit(titleSurf, titleRect)

    pressKeySurf, pressKeyRect = txtObjects('Нажмите любую клавишу для продолжения', basic_font, title_color)
    pressKeyRect.center = (int(window_w / 2), int(window_h / 2) + 100)
    display_surf.blit(pressKeySurf, pressKeyRect)

    while checkKeys() == None:
        pg.display.update()
        fps_clock.tick()


def quitGame():
    for event in pg.event.get(QUIT):  # проверка всех событий, приводящих к выходу из игры
        stopGame()
    for event in pg.event.get(KEYUP):
        if event.key == K_ESCAPE:
            stopGame()
        pg.event.post(event)


def calcSpeed(points):
    # вычисляет уровень
    level = int(points / 10) + 1
    fall_speed = 0.27 - (level * 0.02)
    return level, fall_speed


def getNewFig():
    # возвращает новую фигуру со случайным цветом и углом поворота
    shape = random.choice(list(figures.keys()))
    newFigure = {'shape': shape,
                 'rotation': random.randint(0, len(figures[shape]) - 1),
                 'x': int(cup_w / 2) - int(fig_w / 2),
                 'y': -2,
                 'color': random.randint(0, len(colors) - 1)}
    return newFigure


def addToCup(cup, fig):
    for x in range(fig_w):
        for y in range(fig_h):
            if figures[fig['shape']][fig['rotation']][y][x] != empty:
                cup[x + fig['x']][y + fig['y']] = fig['color']


def emptycup():
    # создает пустой стакан
    cup = []
    for i in range(cup_w):
        cup.append([empty] * cup_h)
    return cup


def incup(x, y):
    return x >= 0 and x < cup_w and y < cup_h


def checkPos(cup, fig, adjX=0, adjY=0):
    # проверяет, находится ли фигура в границах стакана, не сталкиваясь с другими фигурами
    for x in range(fig_w):
        for y in range(fig_h):
            abovecup = y + fig['y'] + adjY < 0
            if abovecup or figures[fig['shape']][fig['rotation']][y][x] == empty:
                continue
            if not incup(x + fig['x'] + adjX, y + fig['y'] + adjY):
                return False
            if cup[x + fig['x'] + adjX][y + fig['y'] + adjY] != empty:
                return False
    return True


def isCompleted(cup, y):
    # проверяем наличие полностью заполненных рядов
    for x in range(cup_w):
        if cup[x][y] == empty:
            return False
    return True


def clearCompleted(cup):
    # Удаление заполенных рядов и сдвиг верхних рядов вниз
    removed_lines = 0
    y = cup_h - 1
    while y >= 0:
        if isCompleted(cup, y):
            for pushDownY in range(y, 0, -1):
                for x in range(cup_w):
                    cup[x][pushDownY] = cup[x][pushDownY - 1]
            for x in range(cup_w):
                cup[x][0] = empty
            removed_lines += 1
        else:
            y -= 1
    return removed_lines


def convertCoords(block_x, block_y):
    return (side_margin + (block_x * block)), (top_margin + (block_y * block))


def drawBlock(block_x, block_y, color, pixelx=None, pixely=None):
    # отрисовка квадратных блоков, из которых состоят фигуры
    if color == empty:
        return
    if pixelx == None and pixely == None:
        pixelx, pixely = convertCoords(block_x, block_y)
    pg.draw.rect(display_surf, colors[color], (pixelx + 1, pixely + 1, block - 1, block - 1), 0, 3)
    pg.draw.rect(display_surf, lightcolors[color], (pixelx + 1, pixely + 1, block - 4, block - 4), 0, 3)
    pg.draw.circle(display_surf, colors[color], (pixelx + block / 2, pixely + block / 2), 5)


def gamecup(cup):
    # граница игрового поля-стакана
    pg.draw.rect(display_surf, brd_color, (side_margin - 4, top_margin - 4, (cup_w * block) + 8, (cup_h * block) + 8),
                 5)

    # фон игрового поля
    pg.draw.rect(display_surf, bg_color, (side_margin, top_margin, block * cup_w, block * cup_h))
    for x in range(cup_w):
        for y in range(cup_h):
            drawBlock(x, y, cup[x][y])


def drawTitle():
    titleSurf = big_font.render('Тетрис Lite', True, title_color)
    titleRect = titleSurf.get_rect()
    titleRect.topleft = (window_w - 425, 30)
    display_surf.blit(titleSurf, titleRect)


def drawInfo(points, level):
    pointsSurf = basic_font.render(f'Баллы: {points}', True, txt_color)
    pointsRect = pointsSurf.get_rect()
    pointsRect.topleft = (window_w - 550, 180)
    display_surf.blit(pointsSurf, pointsRect)

    levelSurf = basic_font.render(f'Уровень: {level}', True, txt_color)
    levelRect = levelSurf.get_rect()
    levelRect.topleft = (window_w - 550, 250)
    display_surf.blit(levelSurf, levelRect)

    pausebSurf = basic_font.render('Пауза: пробел', True, info_color)
    pausebRect = pausebSurf.get_rect()
    pausebRect.topleft = (window_w - 550, 420)
    display_surf.blit(pausebSurf, pausebRect)

    escbSurf = basic_font.render('Выход: Esc', True, info_color)
    escbRect = escbSurf.get_rect()
    escbRect.topleft = (window_w - 550, 450)
    display_surf.blit(escbSurf, escbRect)


def drawFig(fig, pixelx=None, pixely=None):
    figToDraw = figures[fig['shape']][fig['rotation']]
    if pixelx == None and pixely == None:
        pixelx, pixely = convertCoords(fig['x'], fig['y'])

    # отрисовка элементов фигуру
    for x in range(fig_w):
        for y in range(fig_h):
            if figToDraw[y][x] != empty:
                drawBlock(None, None, fig['color'], pixelx + (x * block), pixely + (y * block))


def drawnextFig(fig):  # превью следующей фигуры
    nextSurf = basic_font.render('Следующая:', True, txt_color)
    nextRect = nextSurf.get_rect()
    nextRect.topleft = (window_w - 150, 180)
    display_surf.blit(nextSurf, nextRect)
    drawFig(fig, pixelx=window_w - 150, pixely=230)


if __name__ == '__main__':
    main()
```

## 21. tgbot2.py
```python
# ================================================
# Number Guess — бот "Угадай число"
# Библиотека: pyTelegramBotAPI (telebot)
# ================================================

import telebot
import random

TOKEN = ""
bot = telebot.TeleBot(TOKEN)

# ================================================
# ХРАНЕНИЕ СОСТОЯНИЯ ИГРОКОВ
# ================================================

players = {}  # ключ: user_id → словарь с данными игры


def init_player(user_id):
    """Инициализация нового игрока"""
    players[user_id] = {
        "state": "idle",  # idle → playing → finished
        "secret_number": None,  # загаданное число
        "attempts": 0,  # количество попыток
        "max_attempts": 5  # лимит попыток
    }


# ================================================
# КОМАНДА /start
# ================================================

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if user_id not in players:
        init_player(user_id)

    bot.send_message(
        message.chat.id,
        "🎮 Добро пожаловать в «Угадай число»!\n\n"
        "Я загадаю число от 1 до 100.\n"
        "У тебя 5 попыток, чтобы угадать.\n\n"
        "👉 Начать игру: /play\n"
        "📊 Статистика: /stats"
    )


# ================================================
# КОМАНДА /play — начало игры
# ================================================

@bot.message_handler(commands=['play'])
def play(message):
    user_id = message.from_user.id
    if user_id not in players:
        init_player(user_id)

    # Генерируем новое число и сбрасываем счётчик
    players[user_id]["secret_number"] = random.randint(1, 100)
    players[user_id]["attempts"] = 0
    players[user_id]["state"] = "playing"

    bot.send_message(
        message.chat.id,
        "🎲 Я загадал число от 1 до 100!\n"
        "Напиши свой вариант:"
    )


# ================================================
# КОМАНДА /stats — статистика
# ================================================

@bot.message_handler(commands=['stats'])
def stats(message):
    user_id = message.from_user.id
    if user_id not in players:
        bot.send_message(message.chat.id, "Сначала напиши /start")
        return

    player = players[user_id]
    bot.send_message(
        message.chat.id,
        f"📊 Твоя статистика:\n"
        f"Попыток использовано: {player['attempts']}\n"
        f"Лимит попыток: {player['max_attempts']}"
    )


# ================================================
# ОБРАБОТКА ОТВЕТОВ В ИГРЕ
# ================================================

@bot.message_handler(func=lambda message: True)
def handle_guess(message):
    user_id = message.from_user.id
    if user_id not in players:
        return

    player = players[user_id]

    # Обрабатываем ТОЛЬКО когда игрок в игре
    if player["state"] != "playing":
        return

    # Проверяем, что введено число
    try:
        guess = int(message.text.strip())
    except ValueError:
        bot.send_message(message.chat.id, "🔢 Введи целое число (например: 42)")
        return

    # Увеличиваем счётчик попыток
    player["attempts"] += 1
    secret = player["secret_number"]

    # Логика сравнения
    if guess == secret:
        bot.send_message(
            message.chat.id,
            f"🎉 Поздравляю! Ты угадал за {player['attempts']} попыток!\n"
            "Хочешь сыграть ещё? Напиши /play"
        )
        player["state"] = "finished"
        return

    # Подсказки
    if guess < secret:
        hint = "🔼 Моё число больше"
    else:
        hint = "🔽 Моё число меньше"

    # Проверяем лимит попыток
    remaining = player["max_attempts"] - player["attempts"]
    if remaining <= 0:
        bot.send_message(
            message.chat.id,
            f"❌ Попытки закончились! Загаданное число было: {secret}\n"
            "Сыграем ещё? /play"
        )
        player["state"] = "finished"
    else:
        bot.send_message(
            message.chat.id,
            f"{hint}\n"
            f"Осталось попыток: {remaining}"
        )


# ================================================
# ЗАПУСК БОТА
# ================================================

if __name__ == "__main__":
    print("🚀 Number Guess запущен! Нажми Ctrl+C чтобы остановить.")
    bot.polling(none_stop=True)
```

## 22. пара.py
```python
import telebot

TOKEN = "8222974164:AAHyQwMxNeZv1A17zqVrHx1ovpC_QBvJuZo"

bot = telebot.TeleBot(TOKEN)

users = {}

"""

users - словарь 
Словарь - англо-русский словарь там можно посмотреть пару значений  

словарь['xp'] -> 0

"""


def init_user(user_id):
    users[user_id] = {
        "level": 1,
        "xp": 0,
        "state": "intro"
    }


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id

    if user_id in users:
        init_user(user_id)

    # print(message)

    bot.send_message(message.chat.id,
                     "Добро пожаловать! Ты начинающий программист Python.\nТвоя цель прокачать навыки и пройти путь разработчкиа\nНапиши /play чтобы начать игру")


@bot.message_handler(commands=['play'])
def play(message):
    user_id = message.from_user.id

    if user_id not in users:
        init_user(user_id)

    users[user_id]["state"] = "question_1"

    bot.send_message(message.chat.id, "Вопрос 1\n"
                                      "Как правильно вывести текст в Python\n"
                                      "A) print('Hello')"
                                      "B) echo('Hello')"
                                      "С) console.log('Hello')")


@bot.message_handler(func=lambda message: True)
def hanle_game(message):
    user_id = message.from_user.id

    text = message.text.strip().upper()

    if user_id not in users:
        return
    user = users[user_id]

    if user['state'].startswith('questions_'):
        q_num = int(user['state'].split('_')[1])


bot.polling(none_stop=True)
```

## 23. tg bot.py
```python
# ================================================
# CodeQuest — учебный Telegram-бот игра про Python
# Библиотека: pyTelegramBotAPI (telebot)
# ================================================

# --- Импорт библиотеки для работы с Telegram ---
import telebot  # основная библиотека для создания Telegram-ботов

# --- ТОКЕН БОТА ---
# Вставь сюда токен, который ты получил у @BotFather
TOKEN = "PASTE_YOUR_TOKEN_HERE"

# --- Создание объекта бота ---
bot = telebot.TeleBot(TOKEN)

# --- Хранилище данных пользователей ---
# Ключ словаря — id пользователя
# Значение — словарь с игровыми данными
users = {}

# ================================================
# ФУНКЦИЯ: инициализация нового игрока
# ================================================

def init_user(user_id):
    """
    Создаёт нового игрока с начальными параметрами
    """
    users[user_id] = {
        "level": 1,          # уровень игрока
        "xp": 0,             # опыт
        "state": "intro"    # текущее состояние игры
    }

# ================================================
# КОМАНДА /start
# ================================================

@bot.message_handler(commands=['start'])
def start(message):
    # Получаем уникальный id пользователя
    user_id = message.from_user.id

    # Если пользователь новый — инициализируем его
    if user_id not in users:
        init_user(user_id)

    # Отправляем приветственное сообщение
    bot.send_message(
        message.chat.id,
        "👋 Добро пожаловать в CodeQuest!\n\n"
        "Ты начинающий программист Python 🐍\n"
        "Твоя цель — прокачать навыки и пройти путь разработчика.\n\n"
        "Напиши /play чтобы начать игру."
    )

# ================================================
# КОМАНДА /play — запуск игры
# ================================================

@bot.message_handler(commands=['play'])
def play(message):
    user_id = message.from_user.id

    # Проверка: если пользователь ещё не нажал /start
    if user_id not in users:
        init_user(user_id)

    # Устанавливаем состояние "вопрос 1"
    users[user_id]["state"] = "question_1"

    # Отправляем первый вопрос
    bot.send_message(
        message.chat.id,
        "❓ Вопрос 1\n"
        "Как правильно вывести текст в Python?\n\n"
        "A) print('Hello')\n"
        "B) echo('Hello')\n"
        "C) console.log('Hello')"
    )

# ================================================
# ОБРАБОТКА ОТВЕТОВ ПОЛЬЗОВАТЕЛЯ
# ================================================

# ================================================
# ЕДИНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ (БЕЗ ДУБЛИРОВАНИЯ)
# ================================================

@bot.message_handler(func=lambda message: True)
def handle_game(message):
    # Получаем id пользователя
    user_id = message.from_user.id

    # Получаем текст сообщения и приводим к верхнему регистру
    text = message.text.strip().upper()

    # Если пользователь ещё не зарегистрирован — игнорируем
    if user_id not in users:
        return

    # Получаем данные пользователя
    user = users[user_id]

    # Проверяем, находится ли пользователь в состоянии вопроса
    if user['state'].startswith('question_'):
        # Извлекаем номер текущего вопроса
        q_num = int(user['state'].split('_')[1])

        # Проверяем правильность ответа
        if text == questions[q_num]['answer']:
            # Начисляем опыт
            user['xp'] += 10

            # Сообщаем о правильном ответе
            bot.send_message(message.chat.id, "✅ Верно! +10 XP")

            # Если следующий вопрос существует — переходим к нему
            if q_num + 1 in questions:
                user['state'] = f"question_{q_num + 1}"
                send_question(message.chat.id, q_num + 1)
            else:
                # Если вопросы закончились — завершаем уровень
                user['state'] = "finished"
                user['level'] += 1

                bot.send_message(
                    message.chat.id,
                    "🏆 Уровень пройден!"
                    f"Текущий уровень: {user['level']}"
                    f"Опыт: {user['xp']} XP"
                )
        else:
            # Неправильный ответ
            bot.send_message(message.chat.id, "❌ Неверно. Попробуй ещё раз.")

# ================================================
# ЗАПУСК БОТА
# ================================================

bot.polling(none_stop=True)

# ================================================

# polling — постоянный опрос серверов Telegram
# none_stop=True — бот не остановится при ошибке
# ================================================
# ДОПОЛНИТЕЛЬНЫЕ УРОВНИ И МЕХАНИКИ ИГРЫ
# ================================================

# Словарь с вопросами (можно легко расширять)
questions = {
    1: {
        "question": "Как правильно вывести текст в Python?",
        "options": ["A) print('Hello')", "B) echo('Hello')", "C) console.log('Hello')"],
        "answer": "A"
    },
    2: {
        "question": "Какой тип данных используется для текста?",
        "options": ["A) int", "B) str", "C) float"],
        "answer": "B"
    },
    3: {
        "question": "Как создать список в Python?",
        "options": ["A) {}", "B) ()", "C) []"],
        "answer": "C"
    }
}

# ================================================
# КОМАНДА /stats — статистика игрока
# ================================================

@bot.message_handler(commands=['stats'])
def stats(message):
    user_id = message.from_user.id

    if user_id not in users:
        bot.send_message(message.chat.id, "Сначала напиши /start")
        return

    user = users[user_id]

    bot.send_message(
        message.chat.id,
        f"📊 Твоя статистика:"
        f"Уровень: {user['level']}"
        f"Опыт: {user['xp']} XP"
    )

# ================================================
# ОБНОВЛЁННАЯ ОБРАБОТКА ОТВЕТОВ (ЦИКЛ ВОПРОСОВ)
# ================================================

@bot.message_handler(func=lambda message: True)
def handle_game(message):
    user_id = message.from_user.id
    text = message.text.strip().upper()

    if user_id not in users:
        return

    user = users[user_id]

    # Проверяем, находится ли игрок в режиме игры
    if user['state'].startswith('question_'):
        # Получаем номер текущего вопроса
        q_num = int(user['state'].split('_')[1])

        # Проверяем ответ
        if text == questions[q_num]['answer']:
            user['xp'] += 10
            bot.send_message(message.chat.id, "✅ Верно! +10 XP")

            # Переход к следующему вопросу
            if q_num + 1 in questions:
                user['state'] = f"question_{q_num + 1}"
                send_question(message.chat.id, q_num + 1)
            else:
                user['state'] = "finished"
                user['level'] += 1
                bot.send_message(
                    message.chat.id,
                    "🏆 Ты прошёл уровень!"
                    f"Текущий уровень: {user['level']}"
                )
        else:
            bot.send_message(message.chat.id, "❌ Неверно. Попробуй ещё раз.")

# ================================================
# ФУНКЦИЯ ОТПРАВКИ ВОПРОСА
# ================================================

def send_question(chat_id, q_num):
    # Получаем данные вопроса по номеру
    q = questions[q_num]

    # Формируем текст вопроса
    text = f"❓ Вопрос {q_num}\n{q['question']}\n\n"

    # Добавляем варианты ответов, каждый с новой строки
    text += "\n".join(q['options'])

    # Отправляем сообщение пользователю
    bot.send_message(chat_id, text)


# ================================================
# ЗАПУСК БОТА
# ================================================

bot.polling(none_stop=True)
```
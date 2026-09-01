
#Глава 3. Функции нескольких переменных.

## 9.1
$$
f(x,y)=x^{2}+xy^{2}+y^{3}

\frac{\partial f}{\partial x} = (x^{2}+y^{2}x+y^{3})_{x}^{'} = 2x+y^{2}

\frac{\partial f}{\partial y} = (x^{2}+xy^{2}+y^{3})_{y}^{'} = 0+2xy + 3y^{2}
$$


## 9.2

$$
z = x^4 + 3x^2 y^5

$$$$\frac{dz}{dx} = (x^4 + 3x^2 y^5)_x = 4x^3 + 6x y^5$$
$$\quad \frac{dz}{dy} = (x^4 + 3x^2 y^5)_y = 15x^2 y^4$$

$$\frac{d^2 z}{dx^2} = (4x^3 + 6xy^5)_x = 12x^2 + 6y^5 \quad \frac{d^2 z}{dy^2} = 60x^2 y^3$$

$$\frac{d^2 z}{dxdy} = (4x^3 + 6xy^5)_y = 30xy^4 \quad \frac{d^2 z}{dydx} = (15x^2 y^4)_x = 30xy^4$$


## 9.3

$$
\text {Теорема (необходимые условия дифф фин)
Пусть функцию z=f(x,y) дифф. в точке Мо(хо,уо)
Тогда она непрерывна в этой точке и имеет
в ней частиные производные по обеим независимым
переменным.}$$$$ \text {Примем fx'(xo, yo) = A fy'(xo, yo) = B
z = f(x,y) - диф. в г. Ао => ∆Z(Mo) = ADX + BAY +α1∆X +α2∆y}
$$
$$
$$
$$\Delta Z(M_0) = A\Delta X + B\Delta Y + \alpha_1 \Delta X + \alpha_2 \Delta Y
$$


$$ \text {Непрерывность -}$$
$$
\lim_{\Delta x \to 0 \atop \Delta y \to 0} \Delta Z(M_0) = \lim_{\Delta x \to 0 \atop \Delta y \to 0} (A \Delta x + B \Delta y + \alpha_1 \Delta x + \alpha_2 \Delta y) = 0
$$
$$
\lim_{\Delta x \to 0 \atop \Delta y \to 0} (z(x_0 + \Delta x, y_0 + \Delta y) - z(x_0, y_0)) = 0 \implies \lim_{\Delta x \to 0 \atop \Delta y \to 0} z(x_0 + \Delta x, y_0 + \Delta y) = z(x_0, y_0)
$$
$$
\text {Достаточность -}
$$
$$f_x'(x_0, y_0) = \frac{dz(M_0)}{dx} = \lim_{\Delta x \to 0} \frac{\Delta Z(M_0)}{\Delta x} = \lim_{\Delta x \to 0} \frac{A \Delta x + \alpha_1 \Delta x}{\Delta x} = A
$$

$$f_x'(x_0, y_0) = \frac{dz(N_0)}{dy} = \lim_{\Delta y \to 0} \frac{B\Delta y + \alpha \Delta y}{\Delta y} = B
$$
## 9.4

$$\text {Функция Z непрерывна в (0;0) и имеет в этой точке}$$
$$\text {частные производные, но не является дифференцируемой}$$
$$Z = x+y+\sqrt{|x|\cdot |y|}
$$
1)$$\lim_{\Delta x \to 0} (x+y+\sqrt{|x|\cdot y}) = 0; Z(0:0) = 0 \Rightarrow Z - \text{непрерывна в.}
$$
2)$$
Z_x'(0,0) = \lim_{\Delta x \to 0} \frac{z(\Delta x;0) - z(0,0)}{\Delta x} = \lim_{\Delta x \to 0} \frac{\Delta x + 0 + \sqrt{|\Delta x|} \cdot 0}{\Delta x}
= \lim_{\Delta x \to 0} \frac{\Delta x}{\Delta x} = 1 \Rightarrow \partial_x z (0,0) = 1
$$

$$
Z_y'(0,0) = \lim_{\Delta y \to 0} \frac{Z(0, \Delta y) - Z(0,0)}{\Delta y} + \lim_{\Delta y \to 0} \frac{\Delta y + \sqrt{0 \cdot \Delta y}}{\Delta y} = 1
$$
3)$$
\Delta z(0;0) = z(\Delta x, \Delta y) - z(0;0) = \Delta x + \Delta y + ... 
$$
$$
Z_x'(0,0) = A; Z_y'(0,0) = B \Rightarrow \Delta X + \Delta Y - \text{лин часть}
$$
$$
\alpha = \frac{\sqrt{|\Delta x| \cdot |y|}}{\beta}
$$
$$ \text {Функция будет дифференцируема при a - б.м. и p стремящегося к 0}
$$
$$\lim_{p \to 0} \alpha = \lim_{p \to 0} \sqrt{\frac{|x| + |y|}{p}} = \lim_{p \to 0} \sqrt{\frac{p|\cos\varphi| - p|\sin\varphi|}{p} }=
$$
$$
= \lim_{\rho \to 0} \sqrt{|\cos \varphi| \cdot |\sin \varphi|} = -\sqrt{|\cos \varphi| \cdot \sin \varphi} - \text{зависит от } \varphi
$$
$$
\Rightarrow \text{необяз}: \neq 0 \Rightarrow \varphi \text{ не дифф}
$$
## Дифференциал ФНП 9.5
$$
z = f(x,y) \quad dz = \frac{\partial z}{\partial x} dx + \frac{\partial z}{\partial y} dy $$
$$d^2 z = d(dz) = \left( \frac{\partial z}{\partial x} dx + \frac{\partial z}{\partial y} dy \right)'_x dx + \left( \frac{\partial z}{\partial x} dx + \frac{\partial z}{\partial y} dy \right)'_y dy = $$
$$= \left( \frac{\partial^2 z}{\partial x^2} dx + \frac{\partial^2 z}{\partial x \partial y} dy \right) dx + \left( \frac{\partial^2 z}{\partial x \partial y} dx + \frac{\partial^2 z}{\partial y^2} dy \right) dy = $$$$\\
= \frac{\partial^2 z}{\partial x^2} (dx)^2 + 2 \frac{\partial^2 z}{\partial x \partial y} dx dy + \frac{\partial^2 z}{\partial y^2} (dy)^2
$$
## 9.6
$$ \text {Теорема 1 о производной сложной функции}$$
$$ \text {Пусть функции дифференцируемы}$$
$$z = f(x,y), \quad x = \psi_1(u,v), \quad y = \psi_2(u,v)
$$
$$
\frac{\partial z}{\partial u} = \frac{\partial z}{\partial x} \cdot \frac{\partial x}{\partial u} + \frac{\partial z}{\partial y} \cdot \frac{\partial y}{\partial u}
$$
$$ \text {ф-ии х и у получают приращения, z дифф. следовательно тоже получает приращение}$$

$$\Delta Z = \frac{\partial Z}{\partial x} \cdot \Delta x + \frac{\partial Z}{\partial y} \cdot \Delta y
+\alpha_1 \Delta_u x + \alpha_2 \Delta_u y \quad \lim_{u} L_2 и L_1 - \delta M \text{ при } \Delta_u x\to 0 \text{ при } \Delta_u y \to 0
$$
$$
\frac{\partial z}{\partial u} = \lim_{\Delta u \to 0} \frac{\Delta z}{\Delta u} = \lim_{\Delta u \to 0} \left( \frac{\partial z}{\partial x} \cdot \frac{\Delta x}{\Delta u} + \frac{\partial z}{\partial y} \cdot \frac{\Delta y}{\Delta u} + \frac{\alpha_1 \Delta  x + \alpha_2 \Delta y}{\Delta u} \right)=$$
$$= \frac{\partial z}{\partial x} \cdot \frac{\partial x}{\partial u} + \frac{\partial z}{\partial y} \cdot \frac{\partial y}{\partial u} + \frac{\partial x}{\partial u} \cdot \lim_{\Delta u \to 0} \alpha_1 + \frac{\partial y}{\partial u} \lim_{\Delta u \to 0} \alpha_2
$$

$$\lim_{u \to 0} \alpha_1 = \lim_{u x \to 0} \alpha_1 \neq 0
$$
$$
\lim_{u \to 0} \alpha_2 = \lim_{u y \to 0} \alpha_2 = 0
$$
$$
\Rightarrow \frac{\partial z}{\partial u} = \frac{\partial z}{\partial x} \cdot \frac{\partial x}{\partial u} + \frac{\partial z}{\partial y} \cdot \frac{\partial y}{\partial u}
$$
## Дифференцирование неявно заданной функции. 

## 9.8
$$\text {Задача: Найти частные производные неявной функции}$$
$$\text{
1) F(x,y) удовлетворяет уравнению условием теоремы 1
в некоторой окрестности P(x₀,y₀)}
$$
$$\text{
Тогда уравнение F(x,y) = 0 определяет в некоторой
окрестности и точни хо непрерывную функцию y=f(x)
}$$

$$
\frac{dy}{dx} = -\frac{F_x}{F_y}
$$
$$
\frac{du}{dx} = \frac{\partial F}{\partial x} + \frac{\partial F}{\partial y} \cdot \frac{dy}{dx} ; \quad \frac{\partial F}{\partial x} + \frac{\partial F}{\partial y} \cdot \frac{dy}{dx} = 0
\Rightarrow \frac{dy}{dx} = -\frac{\frac{\partial F}{\partial x}}{\frac{\partial F}{\partial y}} = -\frac{F_x}{F_y}
$$
## §7 Формула Тейлора для ФНП.

$$
\begin{aligned}
&9.10 \quad f(x, x_2) = x_1^2 + 4x_1 x_2 + 3x_2^2 \\
&A = \begin{pmatrix} 1 & 2 \\ 2 & -3 \end{pmatrix} \\
&f(x_1, x_2, x_3) = 2x_1^2 - x_2^2 + x_3^2 - 6x_1 x_2 - 2x_1 x_3 + 4x_2 x_3 \\
&A = \begin{pmatrix} 2 & -3 & -1 \\ -3 & -1 & 2 \\ -1 & 2 & 1 \end{pmatrix}
\end{aligned}
$$
## §9 Экстремумы.
$$A=\begin{vmatrix}
\frac{\partial^2 f}{\partial x^2} & \frac{\partial^2 f}{\partial x \partial y} \\
\frac{\partial^2 f}{\partial x \partial y} & \frac{\partial^2 f}{\partial y^2}
\end{vmatrix} \quad d^2 f(x,y) = \frac{\partial^2 f}{\partial x^2}(dx)^2 + 2\frac{\partial^2 f}{\partial x \partial y}dxdy + \frac{\partial^2 f}{\partial y^2}(dy)^2$$
## §10 Скалярное поле.
$$\[
\begin{aligned}
& \text { grad } z(M_0) \text { определяет направление, в котором функция } z \text { в точке } M_0 \text { возрастает с наибольшей скоростью. } \\
& \text { При этом } |\operatorname{grad} z(M_0)| \text { равен наибольшей скорости изменения функции в точке } M_0.  \\
&\frac{\partial z}{\partial l}(M_0) = f_x(x_0, y_0) \cos \alpha + f_y(x_0, y_0) \cos \beta = |\operatorname{grad} z(M_0)| \cdot \cos \varphi \\
& = |\operatorname{grad} z(M_0)| \cdot |\cos \alpha \cos \beta| \cdot \cos \varphi = |\operatorname{grad} z(M_0)| \cdot \cos \varphi \\
& \frac{\partial z}{\partial l}(M_0) = |\operatorname{grad} z(M_0)| \cdot \cos \varphi
\end{aligned}
\]$$
$$
\text{a) } \operatorname{grad} z(M_0) - \text{перпендикулярен в линии уровня функция } z = f(x, y), \text{ проходящей через } T. M_0
$$
$$
\text{Линии уровень: } f(x, y) = c \quad \text{Если } r \text{ направление вдоль линии уровня, то } \frac{\partial z(M_0)}{\partial s} = \lim_{|MM_0| \to 0} \frac{1}{|MM_0|} = 0
$$
$$
\text{Из: } \frac{\partial z(M_0)}{\partial s} = |\operatorname{grad} z(M_0)| \cos \varphi \Rightarrow \cos \varphi = 0 \Rightarrow \varphi = \frac{\pi}{2}
$$
$$
\varphi - \text{угол между градиентом и } \vec{s} \Rightarrow \operatorname{grad} M_0 \perp \text{линией уровня}
$$
#Глава 4. Интегрирование функции нескольких переменных

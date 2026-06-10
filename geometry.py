# -*- coding: utf-8 -*-
import math

# Мы импортируем только XYZ из Revit API, так как он нужен для векторов и точек.
# Никаких сложных объектов (документов, элементов) здесь быть не должно.
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import XYZ
from constants import *

def intersect_edge_2d(p1, p2, p3, p4):
    """
    Находит точку пересечения двух 2D-отрезков (игнорируя Z).
    Возвращает параметры t_pipe и t_edge (от 0 до 1), если пересекаются, иначе None.
    """
    dx_p, dy_p = p2.X - p1.X, p2.Y - p1.Y
    dx_e, dy_e = p4.X - p3.X, p4.Y - p3.Y
    dx3, dy3 = p3.X - p1.X, p3.Y - p1.Y
    
    denom = dx_p * dy_e - dy_p * dx_e
    if abs(denom) < 1e-9:
        return None, None
        
    t_pipe = (dx3 * dy_e - dy3 * dx_e) / denom
    t_edge = (dx3 * dy_p - dy3 * dx_p) / denom
    
    # Проверка, что точка лежит на обоих отрезках (с небольшим допуском)
    if -1e-5 <= t_pipe <= 1+1e-5 and -1e-5 <= t_edge <= 1+1e-5:
        return t_pipe, t_edge
    return None, None

def intersect_2d_pipes(p1, p2, p3, p4):
    """
    Находит точку пересечения двух 2D-прямых (заданных парами координат [x,y]).
    Используется для подрезки линий труб на профиле.
    """
    a1 = p2[1] - p1[1]
    b1 = p1[0] - p2[0]
    c1 = a1 * p1[0] + b1 * p1[1]
    
    a2 = p4[1] - p3[1]
    b2 = p3[0] - p4[0]
    c2 = a2 * p3[0] + b2 * p3[1]
    
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-9:
        return None 
    return [(b2 * c1 - b1 * c2) / det, (a1 * c2 - a2 * c1) / det]

def get_first_intersection(P0, D, edges):
    """
    Пускает луч из точки P0 в направлении D и находит ближайшее пересечение с массивом отрезков edges.
    Возвращает кортеж (расстояние t, отметка Z в точке пересечения).
    """
    min_t, best_z = float('inf'), None
    for edge in edges:
        A, B = edge[0], edge[1]
        dx_e, dy_e = B.X - A.X, B.Y - A.Y
        denom = dx_e * D.Y - dy_e * D.X
        
        if abs(denom) < 1e-9:
            continue
            
        t_num = (A.X - P0.X) * (-dy_e) - (A.Y - P0.Y) * (-dx_e)
        u_num = D.X * (A.Y - P0.Y) - D.Y * (A.X - P0.X)
        t, u = t_num / denom, u_num / denom
        
        if -1e-5 <= u <= 1+1e-5 and t > 0.001:
            if t < min_t:
                min_t = t
                best_z = A.Z + u * (B.Z - A.Z)
                
    return (min_t, best_z) if min_t < float('inf') else (None, None)

def slice_edges(pt_start, pt_end, x_start, edges):
    """
    Разрезает список отрезков линией от pt_start до pt_end.
    Возвращает список словарей с координатами {x, z} на профиле.
    """
    local_pts = []
    L_2d = math.sqrt((pt_end.X - pt_start.X)**2 + (pt_end.Y - pt_start.Y)**2)
    if L_2d < 0.001:
        return local_pts
        
    # Заранее считаем габариты (BoundingBox) текущего луча сканирования с микро-запасом
    min_x, max_x = min(pt_start.X, pt_end.X) - 0.01, max(pt_start.X, pt_end.X) + 0.01
    min_y, max_y = min(pt_start.Y, pt_end.Y) - 0.01, max(pt_start.Y, pt_end.Y) + 0.01

    for edge in edges:
        p1, p2 = edge[0], edge[1]
        
        # БЫСТРЫЙ ФИЛЬТР: Отсекаем линии DWG, которые физически далеко от луча
        # Это отсеет 95-99% линий подложки за доли миллисекунды
        if (max_x < min(p1.X, p2.X) or min_x > max(p1.X, p2.X) or 
            max_y < min(p1.Y, p2.Y) or min_y > max(p1.Y, p2.Y)):
            continue

        # Тяжелая математика пересечения вызывается только для тех линий, 
        # которые попали в зону луча
        t_p, t_e = intersect_edge_2d(pt_start, pt_end, p1, p2)
        if t_p is not None:
            calc_x = x_start + t_p * L_2d
            calc_z = p1.Z + t_e * (p2.Z - p1.Z)
            local_pts.append({"x": calc_x, "z": calc_z})
            
    local_pts.sort(key=lambda item: item["x"])
    # Отфильтровываем дубликаты (слишком близкие точки)
    return [{"x": p["x"], "z": p["z"]} for i, p in enumerate(local_pts) if i==0 or p["x"] - local_pts[i-1]["x"] > 0.01]

def get_2d_dir(pA, pB):
    """Возвращает нормализованный 2D вектор (с Z=0) от точки A к точке B."""
    dx, dy = pB.X - pA.X, pB.Y - pA.Y
    L = math.sqrt(dx**2 + dy**2)
    return XYZ(dx/L, dy/L, 0) if L > 0.001 else None

def get_profile_x(pt, raw_data):
    """
    Проецирует 3D точку (pt) на трассу профиля (raw_data) 
    и возвращает ее координату X (пикетаж) на развернутом профиле.
    """
    min_dist = float('inf')
    best_x = 0.0
    for d in raw_data:
        p1, p2 = d["pt_s"], d["pt_e"]
        vX, vY = p2.X - p1.X, p2.Y - p1.Y
        l2 = vX**2 + vY**2
        
        if l2 < 1e-6:
            dist = math.sqrt((pt.X - p1.X)**2 + (pt.Y - p1.Y)**2)
            proj_x = d["x1"]
        else:
            wX, wY = pt.X - p1.X, pt.Y - p1.Y
            t = max(0, min(1, (wX*vX + wY*vY) / l2))
            projX, projY = p1.X + t*vX, p1.Y + t*vY
            dist = math.sqrt((pt.X - projX)**2 + (pt.Y - projY)**2)
            proj_x = d["x1"] + t * (d["x2"] - d["x1"])
            
        if dist < min_dist:
            min_dist = dist
            best_x = proj_x
            
    return best_x

def get_z_on_profile(x_target, profile_pts):
    """Получает отметку Z на профиле по заданному X путем линейной интерполяции."""
    if not profile_pts: return None
    if x_target < profile_pts[0]["x"] - 0.01: return None
    if x_target > profile_pts[-1]["x"] + 0.01: return None
    
    for i in range(len(profile_pts)-1):
        p1, p2 = profile_pts[i], profile_pts[i+1]
        if p1["x"] - 0.01 <= x_target <= p2["x"] + 0.01:
            dx = p2["x"] - p1["x"]
            if abs(dx) < 1e-6: return p1["z"]
            return p1["z"] + (x_target - p1["x"]) * (p2["z"] - p1["z"]) / dx
    return None

def get_exact_pipe_data(x_target, raw_data):
    """
    Ищет параметры трубы (Z центра, диаметр, толщина подушки) для заданной X координаты.
    """
    best_z, best_d, best_c = 0.0, 0.0, 0.1
    min_dist = float('inf')
    
    for d in raw_data:
        if d.get("is_vert", False): continue 
        
        dist1 = abs(d["x1"] - x_target)
        dist2 = abs(d["x2"] - x_target)
        
        if dist1 < min_dist:
            min_dist, best_z, best_d, best_c = dist1, d["z1"], d["d_outer"], d.get("cushion_m", 0.1)
        if dist2 < min_dist:
            min_dist, best_z, best_d, best_c = dist2, d["z2"], d["d_outer"], d.get("cushion_m", 0.1)
            
    return best_z, best_d, best_c

def get_horiz_pipe_z_center(x_target, raw_data):
    """Получает Z центра трубы на конкретном пикете."""
    for d in raw_data:
        if d.get("is_vert"): continue
        x_min, x_max = min(d["x1"], d["x2"]), max(d["x1"], d["x2"])
        if x_min - 0.01 <= x_target <= x_max + 0.01:
            dx = d["x2"] - d["x1"]
            if abs(dx) > 1e-6: 
                return d["z1"] + (x_target - d["x1"]) * (d["z2"] - d["z1"]) / dx
            else: 
                return (d["z1"] + d["z2"]) / 2.0 
                
    # Fallback, если не попали ровно в трубу
    min_dist, best_z = float('inf'), 0.0
    for d in raw_data:
        if d.get("is_vert"): continue
        if abs(x_target - d["x1"]) < min_dist: min_dist, best_z = abs(x_target - d["x1"]), d["z1"]
        if abs(x_target - d["x2"]) < min_dist: min_dist, best_z = abs(x_target - d["x2"]), d["z2"]
    return best_z

# Конвертация единиц
def paper_mm_to_ft(mm, scale_x):
    """Переводит миллиметры на бумаге в футы в модели с учетом масштаба."""
    return (mm / 1000.0) * scale_x / 0.3048

def fmt_slope(val):
    """Форматирует уклон: 10.0 -> 10, 10.5 -> 10,5"""
    s = "{:.1f}".format(val).replace('.', ',')
    if s.endswith(',0'): return s[:-2] 
    return s

def fmt_len(val):
    """Форматирует длину."""
    return "{:.1f}".format(val).replace('.', ',')

def get_dir(s):
    """
    Определяет направление уклона: 
    -1 (вниз), 1 (вверх), 0 (горизонталь).
    Допуск 0.5 промилле позволяет избежать дроблений из-за микро-погрешностей Revit.
    """
    if s < -0.5: 
        return -1
    if s > 0.5: 
        return 1
    return 0
# -*- coding: utf-8 -*-
import clr
import math
import System

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (XYZ, Line, Arc, TextNote, TextNoteOptions, 
                               HorizontalTextAlignment, VerticalTextAlignment,
                               ViewFamilyType, ViewFamily, ViewDrafting, 
                               ElementTransformUtils, ElementId)

# Импортируем нашу конвертацию из модуля геометрии
from geometry import paper_mm_to_ft
from constants import *

def create_drafting_view(doc, view_name, scale):
    """
    Создает новый чертежный вид. Если имя занято, добавляет случайный суффикс.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    
    # Правильный поиск типа чертежного вида через коллектор
    vft = next((v for v in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements() 
                if v.ViewFamily == ViewFamily.Drafting), None)
                
    if not vft:
        raise Exception("Не найден типоразмер для Чертежного вида (Drafting View)!")

    # Создаем сам вид
    new_view = ViewDrafting.Create(doc, vft.Id)
    
    try:
        new_view.Name = view_name
    except Exception:
        # Если вид с таким именем уже существует, добавляем уникальный суффикс
        new_view.Name = view_name + " - " + System.Guid.NewGuid().ToString()[:4]
        
    new_view.Scale = scale
    return new_view

def draw_line(doc, view, xA, yA, xB, yB, style):
    """
    Рисует линию детализации на указанном виде.
    """
    if math.sqrt((xB - xA)**2 + (yB - yA)**2) > 0.003: # Защита от слишком коротких линий (ошибка Revit)
        crv = doc.Create.NewDetailCurve(view, Line.CreateBound(XYZ(xA, yA, 0), XYZ(xB, yB, 0)))
        if style: 
            crv.LineStyle = style
        return crv
    return None

def draw_arc(doc, view, p1, p2, p3, style):
    """
    Рисует дугу (используется для отображения пересекаемых труб).
    """
    try:
        arc = Arc.Create(p1, p2, p3)
        crv = doc.Create.NewDetailCurve(view, arc)
        if style:
            crv.LineStyle = style
        return crv
    except Exception:
        return None

def place_text(doc, view_id, x, y, text, angle_rad, type_id, halign=HorizontalTextAlignment.Center, valign=VerticalTextAlignment.Middle):
    """
    Размещает текстовое примечание (TextNote) с заданным выравниванием и поворотом.
    """
    opt = TextNoteOptions()
    opt.HorizontalAlignment = halign
    opt.VerticalAlignment = valign
    if type_id != ElementId.InvalidElementId: 
        opt.TypeId = type_id
    
    try:
        tn = TextNote.Create(doc, view_id, XYZ(x, y, 0), text, opt)
        if angle_rad != 0.0: 
            # В Revit текст поворачивается вокруг оси Z
            axis = Line.CreateBound(XYZ(x, y, 0), XYZ(x, y, 1))
            ElementTransformUtils.RotateElement(doc, tn.Id, axis, angle_rad)
        return tn
    except Exception:
        return None

def draw_manhole(doc, view, x_center, width_ft, z_bottom, z_top, base_z, distortion_y, style):
    """
    Отрисовывает контур колодца на профиле.
    """
    y_bot = (z_bottom - base_z) * distortion_y
    y_top = (z_top - base_z) * distortion_y
    w = width_ft / 2.0
    
    draw_line(doc, view, x_center-w, y_bot, x_center-w, y_top, style) # Левая стенка
    draw_line(doc, view, x_center+w, y_bot, x_center+w, y_top, style) # Правая стенка
    draw_line(doc, view, x_center-w, y_bot, x_center+w, y_bot, style) # Дно
    draw_line(doc, view, x_center-w, y_top, x_center+w, y_top, style) # Крышка

def create_leader_annotation(doc, view, point_start, target_point, family_symbol, top_text, bot_text, shelf_width_mm):
    """
    Размещает семейство типовой аннотации (выноску) и настраивает ее параметры.
    В твоем шаблоне это семейство "текст с выноской".
    """
    if not family_symbol:
        return None
        
    try:
        if not family_symbol.IsActive: 
            family_symbol.Activate()
            
        inst = doc.Create.NewFamilyInstance(point_start, family_symbol, view)
        
        # Заполнение параметров текста
        p_top = inst.LookupParameter("Текст верх")
        if p_top: p_top.Set(top_text)
        
        p_bot = inst.LookupParameter("Текст низ")
        if p_bot: p_bot.Set(bot_text)
        
        p_sh = inst.LookupParameter("Ширина полки")
        if p_sh:
            try: p_sh.Set(shelf_width_mm / 304.8)
            except Exception: pass
            
        # Добавление выноски (leader)
        try:
            try: inst.addLeader()
            except Exception: inst.AddLeader()
            
            doc.Regenerate() # Обязательно регенерируем модель, чтобы выноска появилась в API
            
            ldrs = None
            try: ldrs = inst.GetLeaders()
            except Exception:
                try: ldrs = inst.get_Leaders()
                except Exception:
                    try: ldrs = inst.Leaders
                    except Exception: pass
            
            if ldrs:
                for l in ldrs:
                    l.End = target_point # Привязываем конец выноски к целевой точке
        except Exception:
            pass
            
        return inst
    except Exception:
        return None
# -*- coding: utf-8 -*-
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (BuiltInParameter, BuiltInCategory, ElementId, 
                               GeometryInstance, Line, PolyLine, GraphicsStyleType, 
                               SetComparisonResult)

from constants import *

def get_pipe_abbr(pipe, doc):
    """Возвращает сокращение для системы (аббревиатуру) трубы."""
    # Обрати внимание: мы импортируем константу, если ты добавил её в constants.py
    # Если нет, просто оставь строку "Система не задана"
    from constants import DEF_SYSTEM 
    
    abbr = ""
    
    # 1. Сначала ищем кастомный параметр экземпляра
    p_abbr = pipe.LookupParameter("Сокращение для системы")
    if p_abbr and p_abbr.HasValue:
        abbr = p_abbr.AsString()
        
    # 2. Если его нет, достаем системную аббревиатуру из типа трубопроводной системы
    if not abbr:
        sys_param = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if sys_param and sys_param.HasValue:
            sys_elem = doc.GetElement(sys_param.AsElementId())
            if sys_elem:
                p_abbr_type = sys_elem.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_ABBREVIATION_PARAM)
                if p_abbr_type and p_abbr_type.HasValue:
                    abbr = p_abbr_type.AsString()
                    
    return abbr if abbr else DEF_SYSTEM

def get_edges_from_layer(doc, geom_elem, layer_name, parent_is_target=False):
    """
    Рекурсивно извлекает отрезки (edges) из геометрии DWG подложки, 
    принадлежащие указанному слою.
    """
    edges = []
    if not geom_elem: 
        return edges
        
    for g in geom_elem:
        is_target = parent_is_target
        if g.GraphicsStyleId != ElementId.InvalidElementId:
            gs = doc.GetElement(g.GraphicsStyleId)
            if gs and gs.GraphicsStyleCategory and gs.GraphicsStyleCategory.Name == layer_name:
                is_target = True
                
        if isinstance(g, GeometryInstance): 
            edges.extend(get_edges_from_layer(doc, g.GetInstanceGeometry(), layer_name, is_target))
        elif is_target:
            if isinstance(g, Line): 
                edges.append((g.GetEndPoint(0), g.GetEndPoint(1)))
            elif isinstance(g, PolyLine):
                pts = g.GetCoordinates()
                for i in range(len(pts)-1): 
                    edges.append((pts[i], pts[i+1]))
                    
    return edges

def get_diameter(element):
    """Возвращает наружный диаметр трубы (в футах)."""
    if element.Category and element.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves):
        p = element.get_Parameter(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
        if not p: 
            p = element.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p and p.HasValue: 
            return p.AsDouble()
    return 0.0

def get_line_style(doc, style_name):
    """Находит стиль линии по имени."""
    try:
        lc = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
        if lc.SubCategories.Contains(style_name): 
            return lc.SubCategories.get_Item(style_name).GetGraphicsStyle(GraphicsStyleType.Projection)
    except Exception:
        pass
    return None

def are_connected(v1, v2):
    """
    Универсальная проверка: соединены ли два элемента (трубы, колодцы, оборудование).
    Сначала проверяет логические коннекторы (даже через фитинги), 
    затем физическое пересечение или касание геометрии.
    """
    # Базовая защита от пустых объектов
    if not v1 or not v2: 
        return False

    id1, id2 = v1.Id, v2.Id
    
    # Безопасная проверка категорий
    is_p1 = v1.Category and v1.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
    is_p2 = v2.Category and v2.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
    
    # --- 1. ЛОГИЧЕСКАЯ ПРОВЕРКА (Коннекторы) ---
    def get_logical(elem):
        res = set()
        cm = None
        
        # Строгая и безопасная проверка свойств вместо слепого try/except
        if hasattr(elem, "ConnectorManager") and elem.ConnectorManager:
            cm = elem.ConnectorManager
        elif hasattr(elem, "MEPModel") and elem.MEPModel and elem.MEPModel.ConnectorManager:
            cm = elem.MEPModel.ConnectorManager
            
        if cm:
            for c in cm.Connectors:
                if c.IsConnected:
                    for r in c.AllRefs:
                        if r.Owner.Id != elem.Id:
                            res.add(r.Owner.Id)
                            
                            # Если соединены через фитинг, смотрим дальше (безопасный спуск)
                            if r.Owner.Category and r.Owner.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeFitting):
                                if hasattr(r.Owner, "MEPModel") and r.Owner.MEPModel and r.Owner.MEPModel.ConnectorManager:
                                    fcm = r.Owner.MEPModel.ConnectorManager
                                    for fc in fcm.Connectors:
                                        if fc.IsConnected:
                                            for fr in fc.AllRefs:
                                                if fr.Owner.Id not in [r.Owner.Id, elem.Id]:
                                                    res.add(fr.Owner.Id)
        return res
        
    if id2 in get_logical(v1): 
        return True
    
    # --- 2. БЕЗОПАСНОЕ ИЗВЛЕЧЕНИЕ ОСЕЙ ---
    def get_curve(elem):
        if hasattr(elem, "Location") and elem.Location:
            # Проверяем, что Location это линия (Curve), а не точка
            if hasattr(elem.Location, "Curve") and elem.Location.Curve:
                return elem.Location.Curve
        return None

    c1 = get_curve(v1)
    c2 = get_curve(v2)

    # --- 3. ПРОВЕРКА ФИЗИЧЕСКОГО ПЕРЕСЕЧЕНИЯ ---
    if is_p1 and is_p2 and c1 and c2:
        for i in range(2):
            pt1 = c1.GetEndPoint(i)
            proj2 = c2.Project(pt1)
            if proj2 and pt1.DistanceTo(proj2.XYZPoint) < 0.05: return True
            
            pt2 = c2.GetEndPoint(i)
            proj1 = c1.Project(pt2)
            if proj1 and pt2.DistanceTo(proj1.XYZPoint) < 0.05: return True
            
        try:
            # Intersect - единственный метод в Revit API, который иногда падает сам по себе 
            # на сложных сплайнах, поэтому тут локальный try оставляем оправданно.
            res = c1.Intersect(c2)
            if res == SetComparisonResult.Overlap or res == SetComparisonResult.Subset: 
                return True
        except Exception: 
            pass
            
    # Труба + Колодец
    elif is_p1 and not is_p2 and c1:
        bb = v2.get_BoundingBox(None)
        if bb:
            for i in range(2):
                pt = c1.GetEndPoint(i)
                if (bb.Min.X - 0.05 <= pt.X <= bb.Max.X + 0.05 and 
                    bb.Min.Y - 0.05 <= pt.Y <= bb.Max.Y + 0.05 and 
                    bb.Min.Z - 0.05 <= pt.Z <= bb.Max.Z + 0.05):
                    return True
                    
    # Колодец + Труба
    elif not is_p1 and is_p2 and c2:
        bb = v1.get_BoundingBox(None)
        if bb:
            for i in range(2):
                pt = c2.GetEndPoint(i)
                if (bb.Min.X - 0.05 <= pt.X <= bb.Max.X + 0.05 and 
                    bb.Min.Y - 0.05 <= pt.Y <= bb.Max.Y + 0.05 and 
                    bb.Min.Z - 0.05 <= pt.Z <= bb.Max.Z + 0.05):
                    return True
                    
    return False
# -*- coding: utf-8 -*-
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (BuiltInParameter, BuiltInCategory, ElementId, 
                               GeometryInstance, Line, PolyLine, GraphicsStyleType, 
                               SetComparisonResult)

def get_pipe_abbr(pipe, doc):
    """Возвращает сокращение для системы (аббревиатуру) трубы."""
    abbr = ""
    try:
        p_abbr = pipe.LookupParameter("Сокращение для системы")
        if p_abbr and p_abbr.HasValue:
            abbr = p_abbr.AsString()
    except Exception:
        pass
    
    if not abbr:
        try:
            for p in pipe.Parameters:
                if p.Definition and p.Definition.Name == "Сокращение для системы":
                    abbr = p.AsString()
                    break
        except Exception:
            pass
        
    if not abbr:
        try:
            sys_param = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
            if sys_param and sys_param.HasValue:
                sys_elem = doc.GetElement(sys_param.AsElementId())
                if sys_elem:
                    p_abbr_type = sys_elem.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_ABBREVIATION_PARAM)
                    if p_abbr_type and p_abbr_type.HasValue:
                        abbr = p_abbr_type.AsString()
        except Exception:
            pass
        
    return abbr if abbr else "Система не задана"

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
    id1, id2 = v1.Id, v2.Id
    is_p1 = v1.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
    is_p2 = v2.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
    
    # Внутренняя функция для проверки коннекторов
    def get_logical(elem):
        res = set()
        try:
            cm = None
            if hasattr(elem, "ConnectorManager"): cm = elem.ConnectorManager
            elif hasattr(elem, "MEPModel") and elem.MEPModel: cm = elem.MEPModel.ConnectorManager
            
            if cm:
                for c in cm.Connectors:
                    if c.IsConnected:
                        for r in c.AllRefs:
                            if r.Owner.Id != elem.Id:
                                res.add(r.Owner.Id)
                                # Если соединены через фитинг, смотрим дальше
                                if r.Owner.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeFitting):
                                    if hasattr(r.Owner, "MEPModel") and r.Owner.MEPModel:
                                        fcm = r.Owner.MEPModel.ConnectorManager
                                        if fcm:
                                            for fc in fcm.Connectors:
                                                if fc.IsConnected:
                                                    for fr in fc.AllRefs:
                                                        if fr.Owner.Id != r.Owner.Id and fr.Owner.Id != elem.Id:
                                                            res.add(fr.Owner.Id)
        except Exception:
            pass
        return res
        
    # Проверка через коннекторы
    if id2 in get_logical(v1): 
        return True
    
    # Проверка физического пересечения/касания
    if is_p1 and is_p2:
        c1 = v1.Location.Curve
        c2 = v2.Location.Curve
        for i in range(2):
            pt1 = c1.GetEndPoint(i)
            try:
                proj = c2.Project(pt1)
                if proj and pt1.DistanceTo(proj.XYZPoint) < 0.05: return True
            except Exception: pass
            
            pt2 = c2.GetEndPoint(i)
            try:
                proj = c1.Project(pt2)
                if proj and pt2.DistanceTo(proj.XYZPoint) < 0.05: return True
            except Exception: pass
        try:
            res = c1.Intersect(c2)
            if res == SetComparisonResult.Overlap or res == SetComparisonResult.Subset: 
                return True
        except Exception: pass
        
    elif is_p1 and not is_p2:
        bb = v2.get_BoundingBox(None)
        if bb:
            c1 = v1.Location.Curve
            for i in range(2):
                pt = c1.GetEndPoint(i)
                if bb.Min.X - 0.05 <= pt.X <= bb.Max.X + 0.05 and \
                   bb.Min.Y - 0.05 <= pt.Y <= bb.Max.Y + 0.05 and \
                   bb.Min.Z - 0.05 <= pt.Z <= bb.Max.Z + 0.05:
                    return True
                    
    elif not is_p1 and is_p2:
        bb = v1.get_BoundingBox(None)
        if bb:
            c2 = v2.Location.Curve
            for i in range(2):
                pt = c2.GetEndPoint(i)
                if bb.Min.X - 0.05 <= pt.X <= bb.Max.X + 0.05 and \
                   bb.Min.Y - 0.05 <= pt.Y <= bb.Max.Y + 0.05 and \
                   bb.Min.Z - 0.05 <= pt.Z <= bb.Max.Z + 0.05:
                    return True
                    
    return False
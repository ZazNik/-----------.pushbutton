# -*- coding: utf-8 -*-
__title__ = "Профиль\nГОСТ НВК"
__doc__ = "Создает продольный профиль наружных сетей (НВК) по ГОСТ на основе выделенных элементов и DWG-подложки."

import math
import System
import traceback
from pyrevit import revit, DB, forms
from System.Windows.Forms import DialogResult
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

# Импорт наших модулей
from ui import DwgLayerSelector
import revit_utils
import geometry
import profile_builder

doc = revit.doc
uidoc = revit.uidoc

def main():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Ошибка: Выделите элементы трассы (трубы, колодцы) перед запуском!", exitscript=True)
    
    selected_elements = [doc.GetElement(id) for id in sel_ids]

    try:
        # Используем правильный ObjectType
        picked_ref = uidoc.Selection.PickObject(ObjectType.Element, "Укажите НАЧАЛО трассы")
        start_element = doc.GetElement(picked_ref.ElementId)
        
    except OperationCanceledException:
        # Пользователь просто нажал Esc, отменяем работу без ошибок
        return 
        
    except Exception as e:
        # Если сломалось что-то другое, скрипт не будет молчать!
        print(traceback.format_exc())
        forms.alert("Ошибка при выборе элемента:\n{}".format(e), exitscript=True)

    dwgs_dict = {imp.Category.Name: imp for imp in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance) if imp.Category}
    if not dwgs_dict:
        forms.alert("Нет DWG подложек в проекте!", exitscript=True)

    lc = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    line_styles = sorted([sub.Name for sub in lc.SubCategories])

    pipe_systems_set = set()
    all_doc_pipes_temp = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType().ToElements()
    for p in all_doc_pipes_temp:
        abbr = revit_utils.get_pipe_abbr(p, doc)
        if abbr and abbr != "Система не задана":
            pipe_systems_set.add(abbr)
            
    pipe_systems = sorted(list(pipe_systems_set)) or ["Система не задана"]

    form = DwgLayerSelector(dwgs_dict, line_styles, pipe_systems)
    if form.ShowDialog() != DialogResult.OK:
        return

    with revit.Transaction("Построение профиля НВК ГОСТ"):
        try:
            # --- 1. ПОДГОТОВКА ДАННЫХ И СТИЛЕЙ ---
            geom_opt = DB.Options()
            e_blk = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_blk)
            e_red = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_red) if form.selected_layer_red != "<Нет>" else []
            e_blk_bnd = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_blk + "_граница")
            e_red_bnd = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_red + "_граница") if form.selected_layer_red != "<Нет>" else []
            
            s_blk = revit_utils.get_line_style(doc, form.selected_styles.get("style_blk", "Тонкие линии"))
            s_red = revit_utils.get_line_style(doc, form.selected_styles.get("style_red", "Тонкие линии"))
            s_casing = revit_utils.get_line_style(doc, form.selected_styles.get("style_casing", "Тонкие линии"))
            s_well = revit_utils.get_line_style(doc, form.selected_styles.get("style_manhole", "Тонкие линии"))
            s_ord = revit_utils.get_line_style(doc, form.selected_styles.get("style_ord", "Тонкие линии"))
            s_grid = revit_utils.get_line_style(doc, form.selected_styles.get("style_grid", "Тонкие линии"))
            
            # --- 2. СОРТИРОВКА ЭЛЕМЕНТОВ ---
            main_pipes = []
            casing_pipes = []
            for el in selected_elements:
                if el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves):
                    ptype = doc.GetElement(el.GetTypeId())
                    t_name = ""
                    if ptype:
                        p_name = ptype.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                        if p_name and p_name.HasValue:
                            t_name = p_name.AsString().lower()
                        else:
                            try: t_name = ptype.Name.lower()
                            except: pass
                    if "футляр" in t_name: casing_pipes.append(el)
                    else: main_pipes.append(el)
      
            manholes = [el for el in selected_elements if el.Category and el.Category.Id.IntegerValue in [int(DB.BuiltInCategory.OST_GenericModel), int(DB.BuiltInCategory.OST_MechanicalEquipment)]]
            
            if not main_pipes: raise Exception("Среди выделенных элементов нет основных труб!")

            # --- 3. ТОПОЛОГИЯ ТРАССЫ ---
            nodes = main_pipes + manholes
            adj = {n.Id: [] for n in nodes}
            for i in range(len(nodes)):
                for j in range(i+1, len(nodes)):
                    if revit_utils.are_connected(nodes[i], nodes[j]):
                        adj[nodes[i].Id].append(nodes[j].Id)
                        adj[nodes[j].Id].append(nodes[i].Id)
                        
            picked_id = start_element.Id
            if picked_id not in [n.Id for n in nodes]:
                raise Exception("Ошибка: Выбранный начальный элемент не входит в список выделенных!")

            # Умный обход (DFS): ищем самый длинный маршрут от стартового элемента.
            # Это спасает от "ложных разветвлений" (когда перепадная труба физически 
            # касается колодца и алгоритм видит кольцо/треугольник).
            def find_longest_path(current_id, visited):
                longest = []
                for nxt in adj[current_id]:
                    if nxt not in visited:
                        # Рекурсивно ищем путь дальше, передавая копию множества посещенных
                        sub_path = find_longest_path(nxt, visited | {nxt})
                        if len(sub_path) > len(longest):
                            longest = sub_path
                return [current_id] + longest

            best_path = find_longest_path(picked_id, {picked_id})

            if len(best_path) < 2:
                raise Exception("Ошибка: Не удалось выстроить цепь элементов. Проверьте соединения.")

            ordered_nodes = [doc.GetElement(nid) for nid in best_path]
            o_pipes = [n for n in ordered_nodes if n.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)]

            # --- 4. РАСЧЕТ ОТРЕЗКОВ И ГЕОМЕТРИИ (raw_d) ---
            r_segs = []
            ref_pt = None
            if o_pipes:
                first_node = ordered_nodes[0]
                first_pipe = o_pipes[0]
                e0 = first_pipe.Location.Curve.GetEndPoint(0)
                e1 = first_pipe.Location.Curve.GetEndPoint(1)
                
                # Логика определения направления первой трубы
                pt_s, pt_e = e0, e1
                if first_node.Id == first_pipe.Id and len(ordered_nodes) > 1:
                    next_node = ordered_nodes[1]
                    if next_node.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves):
                        ne0, ne1 = next_node.Location.Curve.GetEndPoint(0), next_node.Location.Curve.GetEndPoint(1)
                        pt_s, pt_e = (e0, e1) if min(e1.DistanceTo(ne0), e1.DistanceTo(ne1)) < min(e0.DistanceTo(ne0), e0.DistanceTo(ne1)) else (e1, e0)
                    else:
                        bb = next_node.get_BoundingBox(None)
                        if bb:
                            c_mh = (bb.Min + bb.Max) / 2.0
                            pt_s, pt_e = (e0, e1) if e1.DistanceTo(c_mh) < e0.DistanceTo(c_mh) else (e1, e0)
                elif first_node.Id != first_pipe.Id:
                    bb = first_node.get_BoundingBox(None)
                    if bb:
                        c_mh = (bb.Min + bb.Max) / 2.0
                        pt_s, pt_e = (e0, e1) if e0.DistanceTo(c_mh) < e1.DistanceTo(c_mh) else (e1, e0)

                ref_pt = pt_e
                r_segs.append({"s": pt_s, "e": pt_e, "g": 0.0, "d": revit_utils.get_diameter(first_pipe), "pipe": first_pipe, "abbr": revit_utils.get_pipe_abbr(first_pipe, doc)})
                
                for p in o_pipes[1:]:
                    e0, e1 = p.Location.Curve.GetEndPoint(0), p.Location.Curve.GetEndPoint(1)
                    pt_s, pt_e = (e0, e1) if e0.DistanceTo(ref_pt) < e1.DistanceTo(ref_pt) else (e1, e0)
                    g_len = math.sqrt((ref_pt.X-pt_s.X)**2 + (ref_pt.Y-pt_s.Y)**2)
                    r_segs.append({"s": pt_s, "e": pt_e, "g": g_len, "d": revit_utils.get_diameter(p), "pipe": p, "abbr": revit_utils.get_pipe_abbr(p, doc)})
                    ref_pt = pt_e

            dir_f = geometry.get_2d_dir(r_segs[0]["s"], r_segs[0]["e"]) or DB.XYZ(1,0,0) if r_segs else DB.XYZ(1,0,0)
            dir_l = geometry.get_2d_dir(r_segs[-1]["s"], r_segs[-1]["e"]) or DB.XYZ(1,0,0) if r_segs else DB.XYZ(1,0,0)

            raw_d, pts_b, pts_r, bound_xs = [], [], [], []
            cur_x = 0.0
            cur_ref = r_segs[0]["s"] if r_segs else DB.XYZ.Zero
            
            # --- СКАНИРОВАНИЕ ДО НАЧАЛА ТРАССЫ (15 метров назад) ---
            EXT_DIST = 15.0 / 0.3048 
            if r_segs:
                pt_s_ext = r_segs[0]["s"] - dir_f * EXT_DIST
                pts_b.extend(geometry.slice_edges(pt_s_ext, r_segs[0]["s"], -EXT_DIST, e_blk))
                if e_red: pts_r.extend(geometry.slice_edges(pt_s_ext, r_segs[0]["s"], -EXT_DIST, e_red))
                for bp in geometry.slice_edges(pt_s_ext, r_segs[0]["s"], -EXT_DIST, e_blk_bnd): bound_xs.append(bp["x"])
                if e_red_bnd:
                    for rp in geometry.slice_edges(pt_s_ext, r_segs[0]["s"], -EXT_DIST, e_red_bnd): bound_xs.append(rp["x"])

            # --- Сбор данных по трубам ---
            for i, seg in enumerate(r_segs):
                if i > 0 and seg["g"] > 0:
                    pts_b.extend(geometry.slice_edges(cur_ref, seg["s"], cur_x, e_blk))
                    if e_red: pts_r.extend(geometry.slice_edges(cur_ref, seg["s"], cur_x, e_red))
                    for bp in geometry.slice_edges(cur_ref, seg["s"], cur_x, e_blk_bnd): bound_xs.append(bp["x"])
                    if e_red_bnd:
                        for rp in geometry.slice_edges(cur_ref, seg["s"], cur_x, e_red_bnd): bound_xs.append(rp["x"])
                        
                cur_x += seg["g"]
                x1 = cur_x
                plen = math.sqrt((seg["s"].X-seg["e"].X)**2 + (seg["s"].Y-seg["e"].Y)**2)
                x2 = cur_x + plen
                
                pts_b.extend(geometry.slice_edges(seg["s"], seg["e"], x1, e_blk))
                if e_red: pts_r.extend(geometry.slice_edges(seg["s"], seg["e"], x1, e_red))
                for bp in geometry.slice_edges(seg["s"], seg["e"], x1, e_blk_bnd): bound_xs.append(bp["x"])
                if e_red_bnd:
                    for rp in geometry.slice_edges(seg["s"], seg["e"], x1, e_red_bnd): bound_xs.append(rp["x"])
                
                is_v = False
                p_slope = seg["pipe"].get_Parameter(DB.BuiltInParameter.RBS_PIPE_SLOPE)
                if p_slope and p_slope.HasValue and ("рассчит" in p_slope.AsValueString().lower() or "comput" in p_slope.AsValueString().lower()): is_v = True
                if not is_v and plen < 0.05: is_v = True 
                
                type_comments, size_str, thick_str, base_str, h_val_str = "", "", "", "Основание не задано", "100"
                cushion_m = 0.1
                
                p_type_id = seg["pipe"].GetTypeId()
                if p_type_id != DB.ElementId.InvalidElementId:
                    p_type = doc.GetElement(p_type_id)
                    tc_param = p_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS)
                    if tc_param and tc_param.HasValue: type_comments = tc_param.AsString()
                        
                size_p = seg["pipe"].LookupParameter("Размер") or seg["pipe"].get_Parameter(DB.BuiltInParameter.RBS_CALCULATED_SIZE_STRING)
                if size_p and size_p.HasValue: size_str = size_p.AsString()
                    
                thick_p = seg["pipe"].LookupParameter("ADSK_Толщина стенки") or (doc.GetElement(p_type_id).LookupParameter("ADSK_Толщина стенки") if p_type_id != DB.ElementId.InvalidElementId else None)
                if thick_p and thick_p.HasValue:
                    if thick_p.StorageType == DB.StorageType.Double:
                        thick_str = "{:.1f}".format(thick_p.AsDouble() * 304.8).replace('.', ',').rstrip(',0')
                    else: thick_str = thick_p.AsString() or thick_p.AsValueString()

                desc = u"{} {}x{}".format(type_comments, size_str, thick_str).strip() if thick_str else u"{} {}".format(type_comments, size_str).strip()
                if not desc: desc = u"Труба"

                base_param = seg["pipe"].LookupParameter("Основание прокладки")
                if base_param and base_param.HasValue: base_str = base_param.AsString().replace('\\n', '\n')

                h_param = seg["pipe"].LookupParameter("H основания")
                if h_param and h_param.HasValue:
                    if h_param.StorageType == DB.StorageType.String:
                        try:
                            v_num = float(h_param.AsString().replace(',', '.').replace(' ', '').replace('мм', '').replace('м', ''))
                            cushion_m = v_num / 1000.0
                            h_val_str = str(int(v_num)) if int(v_num) == v_num else str(v_num)
                        except: pass
                    elif h_param.StorageType == DB.StorageType.Double:
                        val_mm = h_param.AsDouble() * 304.8
                        cushion_m = val_mm / 1000.0
                        h_val_str = str(int(round(val_mm))) if abs(val_mm - round(val_mm)) < 1e-4 else "{:.1f}".format(val_mm).replace('.', ',')
                    elif h_param.StorageType == DB.StorageType.Integer:
                        val_mm = h_param.AsInteger()
                        cushion_m = val_mm / 1000.0
                        h_val_str = str(val_mm)

                full_base_text = "{} H={} мм".format(base_str, h_val_str)
                raw_d.append({"x1": x1, "z1": seg["s"].Z, "pt_s": seg["s"], "x2": x2, "z2": seg["e"].Z, "pt_e": seg["e"], "d_outer": seg["d"], "is_vert": is_v, "desc": desc, "base_text": full_base_text, "cushion_m": cushion_m, "abbr": seg["abbr"], "pipe": seg["pipe"]})
                
                cur_ref = seg["e"]
                cur_x = x2

            # --- СКАНИРОВАНИЕ ПОСЛЕ КОНЦА ТРАССЫ (15 метров вперед) ---
            if r_segs:
                pt_e_ext = r_segs[-1]["e"] + dir_l * EXT_DIST
                pts_b.extend(geometry.slice_edges(r_segs[-1]["e"], pt_e_ext, cur_x, e_blk))
                if e_red: pts_r.extend(geometry.slice_edges(r_segs[-1]["e"], pt_e_ext, cur_x, e_red))
                for bp in geometry.slice_edges(r_segs[-1]["e"], pt_e_ext, cur_x, e_blk_bnd): bound_xs.append(bp["x"])
                if e_red_bnd:
                    for rp in geometry.slice_edges(r_segs[-1]["e"], pt_e_ext, cur_x, e_red_bnd): bound_xs.append(rp["x"])

            # --- 5. ПЕРЕСЕЧЕНИЯ И КООРДИНАТЫ X ---
            cross_pipes = []
            main_and_casing_ids = [p.Id for p in main_pipes] + [c.Id for c in casing_pipes]
            all_doc_pipes = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType().ToElements()
            
            # Предварительно собираем данные по колодцам для привязки пересечек И ординат
            mh_data = []
            mh_xs = []
            mh_snap_data = [] # ОБЪЯВЛЯЕМ ПЕРЕМЕННУЮ ЗДЕСЬ, ЧТОБЫ ЕЁ ВИДЕЛ ВЕСЬ СКРИПТ
            
            for el in manholes:
                bb = el.get_BoundingBox(None)
                if bb:
                    c_pt = (bb.Min + bb.Max) / 2.0
                    mx = geometry.get_profile_x(c_pt, raw_d)
                    mh_xs.append(mx)
                    mh_data.append({"el": el, "mx": mx, "c_pt": c_pt})
                    
                    # Вычисляем диагональ BoundingBox в плане (реальный размер колодца)
                    r_ft = math.sqrt((bb.Max.X - bb.Min.X)**2 + (bb.Max.Y - bb.Min.Y)**2) / 2.0
                    # Оставляем запас всего 15 см (а не 1.5 м!), чтобы не засасывать соседние колодцы
                    mh_snap_data.append({
                        "mx": mx, 
                        "r": r_ft + (0.15 / 0.3048),
                        "cx": c_pt.X,
                        "cy": c_pt.Y
                    })

            for cp in all_doc_pipes:
                if cp.Id in main_and_casing_ids: continue
                
                # Исключаем футляры
                cp_type = doc.GetElement(cp.GetTypeId()) if cp.GetTypeId() != DB.ElementId.InvalidElementId else None
                if cp_type:
                    t_name = cp_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString().lower() if cp_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) else (cp_type.Name.lower() if hasattr(cp_type, 'Name') else "")
                    if "футляр" in t_name: continue
                        
                cp_curve = cp.Location.Curve
                if not cp_curve: continue
                cp_s, cp_e = cp_curve.GetEndPoint(0), cp_curve.GetEndPoint(1)
                
                is_added = False
                
                # ЛОГИКА 1: Проверяем, ВХОДИТ ли труба физически или логически в один из колодцев трассы
                for md in mh_data:
                    if revit_utils.are_connected(cp, md["el"]):
                        dist_s = cp_s.DistanceTo(md["c_pt"])
                        dist_e = cp_e.DistanceTo(md["c_pt"])
                        calc_cz = cp_s.Z if dist_s < dist_e else cp_e.Z
                        
                        cross_pipes.append({
                            "x": md["mx"], 
                            "z": calc_cz, 
                            "real_d_out": revit_utils.get_diameter(cp), 
                            "abbr": revit_utils.get_pipe_abbr(cp, doc),
                            "in_manhole": True # <--- Ставим флаг, что труба в колодце
                        })
                        is_added = True
                        break 
                        
                if is_added: continue
                
                # ЛОГИКА 2: Классическое пересечение осей (труба просто пересекает трассу в земле)
                for d in raw_d:
                    t_path, t_cp = geometry.intersect_edge_2d(d["pt_s"], d["pt_e"], cp_s, cp_e)
                    if t_path is not None and t_cp is not None:
                        calc_cx = d["x1"] + t_path * (d["x2"] - d["x1"])
                        calc_cz = cp_s.Z + t_cp * (cp_e.Z - cp_s.Z)
                        
                        cross_pipes.append({
                            "x": calc_cx, 
                            "z": calc_cz, 
                            "real_d_out": revit_utils.get_diameter(cp), 
                            "abbr": revit_utils.get_pipe_abbr(cp, doc),
                            "in_manhole": False # <--- Труба просто в земле
                        })
                        break

            casings_geom = []
            for cp in casing_pipes:
                c_curve = cp.Location.Curve
                cp_s, cp_e = c_curve.GetEndPoint(0), c_curve.GetEndPoint(1)
                x1_c = geometry.get_profile_x(cp_s, raw_d)
                x2_c = geometry.get_profile_x(cp_e, raw_d)
                z1_c, z2_c = (cp_e.Z, cp_s.Z) if x1_c > x2_c else (cp_s.Z, cp_e.Z)
                if x1_c > x2_c: x1_c, x2_c = x2_c, x1_c
                casings_geom.append({"x1": x1_c, "z1": z1_c, "x2": x2_c, "z2": z2_c, "d": revit_utils.get_diameter(cp), "pipe": cp})

            cln_b = [p for i, p in enumerate(sorted(pts_b, key=lambda x: x["x"])) if i==0 or p["x"]-pts_b[i-1]["x"]>0.01]
            cln_r = [p for i, p in enumerate(sorted(pts_r, key=lambda x: x["x"])) if i==0 or p["x"]-pts_r[i-1]["x"]>0.01]

            raw_xs = []
            for d in raw_d:
                raw_xs.extend([d["x1"], d["x2"]])
                
            # --- ТОПОЛОГИЧЕСКИЙ ФИЛЬТР ГРАНИЦ ---
            unique_bounds = []
            if bound_xs:
                bound_xs.sort()
                unique_bounds.append(bound_xs[0])
                for bx in bound_xs[1:]:
                    if bx - unique_bounds[-1] > 0.1: # Склеиваем миллиметровые дубликаты
                        unique_bounds.append(bx)
            
            # Четное = трасса "вильнула" и вернулась (аннулируем). Нечетное = пробила (оставляем)
            valid_bounds = list(unique_bounds) if len(unique_bounds) % 2 != 0 else []

            # 1. Защита от выпусков из здания (в начале трассы). 
            # Если граница пересечена в пределах 2.5 метров от старта, убираем ординату.
            if valid_bounds and 0.0 <= valid_bounds[0] <= (2.5 / 0.3048):
                valid_bounds.pop(0)

            # 2. Аналогичная защита для конца трассы.
            if valid_bounds and 0.0 <= (cur_x - valid_bounds[-1]) <= (2.5 / 0.3048):
                valid_bounds.pop()
                
            raw_xs.extend(valid_bounds)
                
            # --- УМНОЕ ПРИМАГНИЧИВАНИЕ ОРДИНАТ К КОЛОДЦАМ ---
            for i in range(len(raw_xs)):
                for md in mh_snap_data:
                    # Если ордината трубы попала в реальные габариты колодца - стягиваем ее ровно в центр
                    if abs(raw_xs[i] - md["mx"]) <= md["r"]:
                        raw_xs[i] = md["mx"]
                        break
            
            raw_xs.sort()
            final_xs = []
            if raw_xs:
                cluster = [raw_xs[0]]
                TOLERANCE = 0.70 / 0.3048 
                for nx in raw_xs[1:]:
                    if nx - cluster[-1] <= TOLERANCE: cluster.append(nx)
                    else: 
                        final_xs.append(sum(cluster) / len(cluster))
                        cluster = [nx]
                if cluster: final_xs.append(sum(cluster) / len(cluster))

                # --- СТРОГАЯ ОБРЕЗКА ОРДИНАТ (от 0.0 до cur_x) ---
                final_xs = [x for x in final_xs if -1e-5 <= x <= cur_x + 1e-5]
                if final_xs:
                    if final_xs[0] > 0.05: final_xs.insert(0, 0.0)
                    if final_xs[-1] < cur_x - 0.05: final_xs.append(cur_x)
                else:
                    final_xs = [0.0, cur_x]

            # --- ГАРАНТИЯ ПОКРЫТИЯ ПОВЕРХНОСТЯМИ ---
            # Если точки поверхности всё ещё не дотягиваются до крайних колодцев, продлеваем крайние отметки горизонтально

            if cln_b and final_xs:
                if final_xs[0] < cln_b[0]["x"]: cln_b.insert(0, {"x": final_xs[0] - 5.0, "z": cln_b[0]["z"]})
                if final_xs[-1] > cln_b[-1]["x"]: cln_b.append({"x": final_xs[-1] + 5.0, "z": cln_b[-1]["z"]})
                
            if cln_r and final_xs:
                if final_xs[0] < cln_r[0]["x"]: cln_r.insert(0, {"x": final_xs[0] - 5.0, "z": cln_r[0]["z"]})
                if final_xs[-1] > cln_r[-1]["x"]: cln_r.append({"x": final_xs[-1] + 5.0, "z": cln_r[-1]["z"]})

            # --- 6. ВИЗУАЛЬНЫЕ НАСТРОЙКИ (Искажение, Base Z) ---
            DISTORTION_Y = float(form.scale_x) / float(form.scale_y)
            all_z = [d["z1"] for d in raw_d] + [d["z2"] for d in raw_d] + [p["z"] for p in cln_b]
            
            # --- ИДЕНТИФИЦИРУЕМ ОТВЕТВЛЕНИЯ (СТОЯКИ) ---
            # Находим трубы, которые были выделены, но не вошли в основную трассу (тройники)
            o_pipe_ids = [op.Id for op in o_pipes]
            branch_pipes = [p for p in main_pipes if p.Id not in o_pipe_ids]
            
            # Добавляем их отметки в all_z, чтобы шкала высот не обрезала стояки
            for bp in branch_pipes:
                c = bp.Location.Curve
                if c: all_z.extend([c.GetEndPoint(0).Z, c.GetEndPoint(1).Z])
                
            if not all_z: raise Exception("Нет отметок (Z)!")
            
            min_z_m = min(all_z) * 0.3048
            base_z_m = float(form.custom_base_z_val) if form.custom_base_z_checked else math.floor(min_z_m - 2.0) 
            base_z = base_z_m / 0.3048

            p_geom = []
            for d in raw_d:
                x1, z1, x2, z2 = d["x1"], d["z1"], d["x2"], d["z2"]
                y1, y2 = (z1 - base_z)*DISTORTION_Y, (z2 - base_z)*DISTORTION_Y
                
                # Считаем нормаль на основе ВИЗУАЛЬНЫХ (искаженных) координат.
                # Это гарантирует, что труба всегда будет казаться одинаковой толщины на чертеже,
                # независимо от её уклона или вертикальности!
                L_vis = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                if L_vis > 0.001:
                    nx_vis = -(y2-y1)/L_vis
                    ny_vis = (x2-x1)/L_vis
                    
                    T_vis = (d["d_outer"] / 2.0) * 1.125 * DISTORTION_Y
                    dx_T = nx_vis * T_vis
                    dy_T = ny_vis * T_vis
                    
                    p_geom.append({
                        "top": [[x1+dx_T, y1+dy_T], [x2+dx_T, y2+dy_T]], 
                        "bot": [[x1-dx_T, y1-dy_T], [x2-dx_T, y2-dy_T]], 
                        "abbr": d["abbr"], "pipe": d["pipe"]
                    })

            # Подчистка углов ТОЛЬКО для основной трассы
            for i in range(len(p_geom) - 1):
                p1, p2 = p_geom[i], p_geom[i+1]
                
                # ЗАЩИТА: Если одна из труб вертикальная, отменяем подчистку углов! 
                if abs(p1["top"][0][0] - p1["top"][1][0]) < 0.05 or abs(p2["top"][0][0] - p2["top"][1][0]) < 0.05:
                    continue
                    
                if revit_utils.are_connected(p1["pipe"], p2["pipe"]):
                    pt_t = geometry.intersect_2d_pipes(p1["top"][0], p1["top"][1], p2["top"][0], p2["top"][1])
                    if pt_t and abs(pt_t[0] - p1["top"][1][0]) < 5.0: 
                        p1["top"][1] = p2["top"][0] = pt_t
                    pt_b = geometry.intersect_2d_pipes(p1["bot"][0], p1["bot"][1], p2["bot"][0], p2["bot"][1])
                    if pt_b and abs(pt_b[0] - p1["bot"][1][0]) < 5.0: 
                        p1["bot"][1] = p2["bot"][0] = pt_b
                        
            # ДОБАВЛЯЕМ ОТВЕТВЛЕНИЯ В ГЕОМЕТРИЮ ПРОФИЛЯ
            for bp in branch_pipes:
                c = bp.Location.Curve
                if not c: continue
                pt0, pt1 = c.GetEndPoint(0), c.GetEndPoint(1)
                
                x0 = geometry.get_profile_x(pt0, raw_d)
                x1 = geometry.get_profile_x(pt1, raw_d)
                z0, z1 = pt0.Z, pt1.Z
                
                y0, y1 = (z0 - base_z)*DISTORTION_Y, (z1 - base_z)*DISTORTION_Y
                
                L_vis = math.sqrt((x1-x0)**2 + (y1-y0)**2)
                if L_vis > 0.001:
                    nx_vis = -(y1-y0)/L_vis
                    ny_vis = (x1-x0)/L_vis
                    
                    d_outer = revit_utils.get_diameter(bp)
                    T_vis = (d_outer / 2.0) * 1.125 * DISTORTION_Y
                    dx_T = nx_vis * T_vis
                    dy_T = ny_vis * T_vis
                    
                    p_geom.append({
                        "top": [[x0+dx_T, y0+dy_T], [x1+dx_T, y1+dy_T]], 
                        "bot": [[x0-dx_T, y0-dy_T], [x1-dx_T, y1-dy_T]], 
                        "abbr": revit_utils.get_pipe_abbr(bp, doc), 
                        "pipe": bp
                    })

            # --- УНИВЕРСАЛЬНОЕ ДОТЯГИВАНИЕ ВСЕХ ВЕРТИКАЛЬНЫХ ТРУБ ДО МАГИСТРАЛЕЙ ---
            # Работает как броня: находит любой стояк и прибивает его низ к горизонтальной трубе
            for p in p_geom:
                # 1. Проверяем, является ли эта линия вертикальной
                if abs(p["top"][0][0] - p["top"][1][0]) < 0.05:
                    x_cen = (p["top"][0][0] + p["bot"][0][0]) / 2.0
                    
                    best_mp = None
                    highest_y = -float('inf')
                    
                    # 2. Ищем магистраль строго под ней
                    for mp in p_geom:
                        if abs(mp["top"][0][0] - mp["top"][1][0]) < 0.05: continue # Пропускаем другие вертикальные
                        
                        mx1 = min(mp["top"][0][0], mp["top"][1][0])
                        mx2 = max(mp["top"][0][0], mp["top"][1][0])
                        
                        if mx1 - 0.5 <= x_cen <= mx2 + 0.5:
                            # Запоминаем самую высокую горизонтальную трубу в этой точке
                            y_mag = (mp["top"][0][1] + mp["top"][1][1]) / 2.0
                            if y_mag > highest_y:
                                highest_y = y_mag
                                best_mp = mp
                                
                    if best_mp:
                        # 3. Вычисляем математически точное пересечение с верхней линией магистрали
                        pt_t = geometry.intersect_2d_pipes(p["top"][0], p["top"][1], best_mp["top"][0], best_mp["top"][1])
                        pt_b = geometry.intersect_2d_pipes(p["bot"][0], p["bot"][1], best_mp["top"][0], best_mp["top"][1])
                        
                        # 4. Жестко подменяем нижнюю координату стояка
                        if pt_t:
                            if p["top"][0][1] < p["top"][1][1]: p["top"][0] = [pt_t[0], pt_t[1]]
                            else: p["top"][1] = [pt_t[0], pt_t[1]]
                        if pt_b:
                            if p["bot"][0][1] < p["bot"][1][1]: p["bot"][0] = [pt_b[0], pt_b[1]]
                            else: p["bot"][1] = [pt_b[0], pt_b[1]]

            # ==========================================
            # 7. СОЗДАНИЕ ВИДА И ОТРИСОВКА
            # ==========================================
            new_view = profile_builder.create_drafting_view(doc, form.view_name, form.scale_x)

            # Вычисляем интервалы колодцев для подрезки по их РЕАЛЬНЫМ габаритам
            mh_intervals = []
            real_mhs = []
            for el in manholes:
                bb = el.get_BoundingBox(None)
                if not bb: continue
                mx = geometry.get_profile_x((bb.Min + bb.Max) / 2.0, raw_d)
                
                # Ищем самую БЛИЖАЙШУЮ ординату с жестким допуском 0.5м
                valid_fx = [fx for fx in final_xs if abs(fx - mx) < (0.5 / 0.3048)]
                real_x = min(valid_fx, key=lambda fx: abs(fx - mx)) if valid_fx else None
                
                if real_x is not None:
                    # Убойная проверка на ковер по всем возможным параметрам
                    is_cover = False
                    names = [el.Name.lower() if el.Name else ""]
                    el_type = doc.GetElement(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None
                    if el_type:
                        if hasattr(el_type, "FamilyName") and el_type.FamilyName: names.append(el_type.FamilyName.lower())
                        sym_name = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                        if sym_name and sym_name.HasValue: names.append(sym_name.AsString().lower())
                    if any("ковер" in n or "ковёр" in n for n in names):
                        is_cover = True

                    w_ft = 0.5 / 0.3048 
                    
                    # Если это НЕ ковер, добавляем зону вырезания труб (чтобы скрыть их внутри колодца)
                    # Коверы трубы не режут, чтобы вертикальный стояк дошел до самого верха!
                    if not is_cover:
                        mh_intervals.append((real_x - w_ft, real_x + w_ft))
                        
                    real_mhs.append({"mx": real_x, "w": w_ft})

            # Локальные функции подрезки
            def extend_to_mhs(pt, other_pt):
                # ЗАЩИТА: Если линия вертикальная, не тянем ее к колодцу
                if abs(pt[0] - other_pt[0]) < 1e-3: 
                    return [pt[0], pt[1]]
                    
                for rm in real_mhs:
                    mx, w = rm["mx"], rm["w"]
                    # Тянем трубу в колодец, ТОЛЬКО если она уже пробила его физическую стенку
                    if abs(pt[0] - mx) <= w + (0.15 / 0.3048) and abs(other_pt[0] - mx) > abs(pt[0] - mx):
                        dx = other_pt[0] - pt[0]
                        if abs(dx) > 1e-5: return [mx, pt[1] + ((other_pt[1] - pt[1]) / dx) * (mx - pt[0])]
                        else: return [mx, pt[1]]
                return [pt[0], pt[1]]

            def draw_clipped_line(xA, yA, xB, yB, style):
                x1, y1, x2, y2 = (xA, yA, xB, yB) if xA <= xB else (xB, yB, xA, yA)
                segs = [[x1, y1, x2, y2]]
                for ml, mr in mh_intervals:
                    new_segs = []
                    for sx1, sy1, sx2, sy2 in segs:
                        if sx2 <= ml + 1e-5 or sx1 >= mr - 1e-5: new_segs.append([sx1, sy1, sx2, sy2])
                        else:
                            dx = sx2 - sx1
                            if sx1 < ml: new_segs.append([sx1, sy1, ml, sy1 + (sy2 - sy1) * (ml - sx1) / dx if dx else sy1])
                            if sx2 > mr: new_segs.append([mr, sy1 + (sy2 - sy1) * (mr - sx1) / dx if dx else sy2, sx2, sy2])
                    segs = new_segs
                for sx1, sy1, sx2, sy2 in segs: profile_builder.draw_line(doc, new_view, sx1, sy1, sx2, sy2, style)

            # Отрисовка труб
            for p in p_geom:
                sys_key = "sys_" + p.get("abbr", "Система не задана")
                s_pipe_seg = revit_utils.get_line_style(doc, form.selected_styles.get(sys_key, "Тонкие линии")) or revit_utils.get_line_style(doc, "Тонкие линии")
                t0, t1 = extend_to_mhs(p["top"][0], p["top"][1]), extend_to_mhs(p["top"][1], p["top"][0])
                b0, b1 = extend_to_mhs(p["bot"][0], p["bot"][1]), extend_to_mhs(p["bot"][1], p["bot"][0])
                draw_clipped_line(t0[0], t0[1], t1[0], t1[1], s_pipe_seg)
                draw_clipped_line(b0[0], b0[1], b1[0], b1[1], s_pipe_seg)
                
            # Отрисовка футляров
            c_geom = []
            casings_geom.sort(key=lambda item: min(item["x1"], item["x2"]))
            for c in casings_geom:
                x1, z1, x2, z2 = c["x1"], c["z1"], c["x2"], c["z2"]
                y1, y2 = (z1 - base_z)*DISTORTION_Y, (z2 - base_z)*DISTORTION_Y
                
                L_vis = math.sqrt((x2-x1)**2 + (y2-y1)**2)
                if L_vis > 0.001:
                    nx_vis = -(y2-y1)/L_vis
                    ny_vis = (x2-x1)/L_vis
                    
                    T_vis = (c["d"] / 2.0) * 1.125 * DISTORTION_Y
                    dx_T = nx_vis * T_vis
                    dy_T = ny_vis * T_vis
                    
                    c_geom.append({
                        "top": [[x1+dx_T, y1+dy_T], [x2+dx_T, y2+dy_T]], 
                        "bot": [[x1-dx_T, y1-dy_T], [x2-dx_T, y2-dy_T]], 
                        "cap_l": True, "cap_r": True, "pipe": c["pipe"]
                    })
                    
            for i in range(len(c_geom) - 1):
                if revit_utils.are_connected(c_geom[i]["pipe"], c_geom[i+1]["pipe"]):
                    pt_t = geometry.intersect_2d_pipes(c_geom[i]["top"][0], c_geom[i]["top"][1], c_geom[i+1]["top"][0], c_geom[i+1]["top"][1])
                    if pt_t and abs(pt_t[0] - c_geom[i]["top"][1][0]) < 5.0: c_geom[i]["top"][1] = c_geom[i+1]["top"][0] = pt_t
                    pt_b = geometry.intersect_2d_pipes(c_geom[i]["bot"][0], c_geom[i]["bot"][1], c_geom[i+1]["bot"][0], c_geom[i+1]["bot"][1])
                    if pt_b and abs(pt_b[0] - c_geom[i]["bot"][1][0]) < 5.0: c_geom[i]["bot"][1] = c_geom[i+1]["bot"][0] = pt_b
                    c_geom[i]["cap_r"] = c_geom[i+1]["cap_l"] = False

            for cg in c_geom:
                t0, t1 = extend_to_mhs(cg["top"][0], cg["top"][1]), extend_to_mhs(cg["top"][1], cg["top"][0])
                b0, b1 = extend_to_mhs(cg["bot"][0], cg["bot"][1]), extend_to_mhs(cg["bot"][1], cg["bot"][0])
                draw_clipped_line(t0[0], t0[1], t1[0], t1[1], s_casing)
                draw_clipped_line(b0[0], b0[1], b1[0], b1[1], s_casing)
                if cg["cap_l"]: draw_clipped_line(t0[0], t0[1], b0[0], b0[1], s_casing)
                if cg["cap_r"]: draw_clipped_line(t1[0], t1[1], b1[0], b1[1], s_casing)

            # Отрисовка земли со строгой обрезкой по краям трассы (0.0 ... cur_x)
            def draw_ground(pts, style):
                for k in range(len(pts) - 1):
                    x1, z1 = pts[k]["x"], pts[k]["z"]
                    x2, z2 = pts[k+1]["x"], pts[k+1]["z"]
                    
                    if x2 < -1e-5 or x1 > cur_x + 1e-5: 
                        continue
                        
                    if x1 < 0.0:
                        z1 = z1 + (z2 - z1) * (0.0 - x1) / (x2 - x1) if x2 != x1 else z1
                        x1 = 0.0
                    if x2 > cur_x:
                        z2 = z1 + (z2 - z1) * (cur_x - x1) / (x2 - x1) if x2 != x1 else z2
                        x2 = cur_x
                        
                    profile_builder.draw_line(doc, new_view, x1, (z1-base_z)*DISTORTION_Y, x2, (z2-base_z)*DISTORTION_Y, style)

            draw_ground(cln_b, s_blk)
            if cln_r: draw_ground(cln_r, s_red)

            # Отрисовка колодцев
            for el in manholes:
                bb = el.get_BoundingBox(None)
                if not bb: continue
                mx = geometry.get_profile_x((bb.Min + bb.Max) / 2.0, raw_d)
                
                # Аналогичный строгий поиск ближайшей оси
                valid_fx = [fx for fx in final_xs if abs(fx - mx) < (0.5 / 0.3048)]
                real_x = min(valid_fx, key=lambda fx: abs(fx - mx)) if valid_fx else None
                
                if real_x is not None:
                    # Та же надежная проверка
                    is_cover = False
                    names = [el.Name.lower() if el.Name else ""]
                    el_type = doc.GetElement(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None
                    if el_type:
                        if hasattr(el_type, "FamilyName") and el_type.FamilyName: names.append(el_type.FamilyName.lower())
                        sym_name = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                        if sym_name and sym_name.HasValue: names.append(sym_name.AsString().lower())
                    if any("ковер" in n or "ковёр" in n for n in names):
                        is_cover = True

                    # Ширина для всех одинаковая (1000 мм)
                    fixed_width = 1.0 / 0.3048
                    
                    if is_cover:
                        # Для ковера фиксируем только ВЫСОТУ (200 мм от верхней крышки)
                        z_top = bb.Max.Z
                        z_bot = z_top - (0.2 / 0.3048) # 0.2 метра (200 мм)
                        profile_builder.draw_manhole(doc, new_view, real_x, fixed_width, z_bot, z_top, base_z, DISTORTION_Y, s_well)
                    else:
                        # Обычный колодец рисуется на всю глубину 
                        profile_builder.draw_manhole(doc, new_view, real_x, fixed_width, bb.Min.Z, bb.Max.Z, base_z, DISTORTION_Y, s_well)

            # ==========================================
            # 8. ПОДВАЛ ГОСТ (Таблица)
            # ==========================================
            txt_type = next((t for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType)), None)
            txt_id = txt_type.Id if txt_type else DB.ElementId.InvalidElementId
            
            row_heights_mm = [15, 15, 15, 15, 15, 15, 10, 10, 10, 10, 15]
            labels = ["Отметка земли проектная, м", "Отметка земли фактическая, м", "Отметка дна траншеи, м", "Отметка верха трубы, м", "Глубина траншеи, м", "Обозначение трубы и тип изоляции", "Основание", "Уклон, ‰ / Длина, м", "Расстояние, м", "Пикет", "Развернутый план"]
            
            y_lines = [0.0] 
            for h in row_heights_mm: y_lines.append(y_lines[-1] - geometry.paper_mm_to_ft(h, form.scale_x))
            
            x_data_start = final_xs[0]
            x_data_end = final_xs[-1]
            x_table_end = x_data_end + (5.0 / 0.3048)
            
            W_gap = geometry.paper_mm_to_ft(20, form.scale_x)
            W_col_left = geometry.paper_mm_to_ft(40, form.scale_x) 
            x_labels_right = x_data_start - W_gap
            x_labels_left = x_labels_right - W_col_left
            
            x_arr = x_labels_right - geometry.paper_mm_to_ft(12, form.scale_x)
            y_arr = y_lines[0]
            h_stem = geometry.paper_mm_to_ft(6, form.scale_x)
            w_line = geometry.paper_mm_to_ft(14, form.scale_x)
            dx_arr = geometry.paper_mm_to_ft(1.0, form.scale_x)
            dy_arr = geometry.paper_mm_to_ft(2.5, form.scale_x)
            
            profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr, y_arr + h_stem, s_grid) 
            profile_builder.draw_line(doc, new_view, x_arr, y_arr + h_stem, x_arr - w_line, y_arr + h_stem, s_grid) 
            profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr - dx_arr, y_arr + dy_arr, s_grid)
            profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr + dx_arr, y_arr + dy_arr, s_grid)
            
            profile_builder.place_text(doc, new_view.Id, x_arr - w_line/2.0, y_arr + h_stem + geometry.paper_mm_to_ft(2, form.scale_x), "{:.2f}".format(base_z_m).replace('.', ','), 0.0, txt_id)
            profile_builder.place_text(doc, new_view.Id, x_labels_left + geometry.paper_mm_to_ft(5, form.scale_x), y_arr + geometry.paper_mm_to_ft(20, form.scale_x), "МГ 1:{}".format(form.scale_x), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left)
            profile_builder.place_text(doc, new_view.Id, x_labels_left + geometry.paper_mm_to_ft(5, form.scale_x), y_arr + geometry.paper_mm_to_ft(15, form.scale_x), "МВ 1:{}".format(form.scale_y), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left)

            for i, lab in enumerate(labels):
                if i == 7: 
                    profile_builder.draw_line(doc, new_view, x_labels_left, y_lines[i], x_labels_right, y_lines[i+1], s_grid)
                    profile_builder.place_text(doc, new_view.Id, x_labels_right - geometry.paper_mm_to_ft(2, form.scale_x), y_lines[i] - geometry.paper_mm_to_ft(1.5, form.scale_x), "Уклон, ‰", 0.0, txt_id, halign=DB.HorizontalTextAlignment.Right, valign=DB.VerticalTextAlignment.Top)
                    profile_builder.place_text(doc, new_view.Id, x_labels_left + geometry.paper_mm_to_ft(2, form.scale_x), y_lines[i+1] + geometry.paper_mm_to_ft(1.5, form.scale_x), "Длина, м", 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left, valign=DB.VerticalTextAlignment.Bottom)
                else:
                    profile_builder.place_text(doc, new_view.Id, (x_labels_left + x_labels_right) / 2.0, (y_lines[i] + y_lines[i+1]) / 2.0, lab, 0.0, txt_id)

            for y in y_lines: profile_builder.draw_line(doc, new_view, x_labels_left, y, x_table_end, y, s_grid)
            profile_builder.draw_line(doc, new_view, x_labels_left, y_lines[0], x_labels_left, y_lines[-1], s_grid) 
            profile_builder.draw_line(doc, new_view, x_labels_right, y_lines[0], x_labels_right, y_lines[-1], s_grid) 
            profile_builder.draw_line(doc, new_view, x_table_end, y_lines[0], x_table_end, y_lines[-1], s_grid) 
            profile_builder.draw_line(doc, new_view, x_data_start, y_lines[5], x_data_start, y_lines[-1], s_grid)
            profile_builder.draw_line(doc, new_view, x_data_end, y_lines[5], x_data_end, y_lines[-1], s_grid)

            # Шкала высот
            temp_all_z = list(all_z)
            if cln_r: temp_all_z.extend([p["z"] for p in cln_r])
            max_z_overall_m = max(temp_all_z) * 0.3048
            top_z_m = int(math.ceil(max_z_overall_m)) + 1
            start_z_m = int(math.floor(base_z_m))
            y_scale_top = ((top_z_m / 0.3048) - base_z) * DISTORTION_Y
            profile_builder.draw_line(doc, new_view, x_labels_right, y_lines[0], x_labels_right, y_scale_top, s_grid)
            tick_len = geometry.paper_mm_to_ft(2, form.scale_x)
            for z_m in range(start_z_m, top_z_m + 1):
                if z_m <= base_z_m + 0.01: continue
                y_tick = ((float(z_m) / 0.3048) - base_z) * DISTORTION_Y
                profile_builder.draw_line(doc, new_view, x_labels_right, y_tick, x_labels_right - tick_len, y_tick, s_grid)
                profile_builder.place_text(doc, new_view.Id, x_labels_right - tick_len - geometry.paper_mm_to_ft(1, form.scale_x), y_tick, "{:.2f}".format(float(z_m)).replace('.', ','), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Right)

            # Аннотации (семейство)
            sym_an = None
            for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_GenericAnnotation).ToElements():
                try:
                    if "текст с выноской" in (s.FamilyName.lower() if s.FamilyName else "") and "текст с выноской" in (s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString().lower() if s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) else ""):
                        sym_an = s; break
                except: pass

            placed_labels = []

            # Выноски футляров
            for c in casings_geom:
                try:
                    cp = c["pipe"]
                    cx, cy = (c["x1"] + c["x2"]) / 2.0, ((c["z1"] + c["z2"]) / 2.0 - base_z) * DISTORTION_Y
                    c_type = doc.GetElement(cp.GetTypeId()) if cp.GetTypeId() != DB.ElementId.InvalidElementId else None
                    top_text = (c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).AsString().strip() if c_type and c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS) and c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).HasValue else "Футляр") or "Футляр"
                    
                    size_p = cp.LookupParameter("Размер") or cp.get_Parameter(DB.BuiltInParameter.RBS_CALCULATED_SIZE_STRING)
                    size_str = size_p.AsString() if size_p and size_p.HasValue else ""
                    
                    thick_p = cp.LookupParameter("ADSK_Толщина стенки") or (doc.GetElement(cp.GetTypeId()).LookupParameter("ADSK_Толщина стенки") if cp.GetTypeId() != DB.ElementId.InvalidElementId else None)
                    thick_str = "{:.1f}".format(thick_p.AsDouble() * 304.8).replace('.', ',').rstrip(',0') if thick_p and thick_p.HasValue and thick_p.StorageType == DB.StorageType.Double else (thick_p.AsString() or thick_p.AsValueString() if thick_p else "")
                    
                    len_p = cp.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
                    len_str = "{:.1f}".format((len_p.AsDouble() * 0.3048) if len_p and len_p.HasValue else 0.0).replace('.', ',').rstrip(',0')
                    bot_text = u"{}x{}, L={} м".format(size_str, thick_str, len_str) if thick_str else u"{}, L={} м".format(size_str, len_str)
                    
                    calc_len_mm = max(len(top_text), len(bot_text)) * 1.8 + 2.0
                    p_sh_x, p_sh_y = cx + geometry.paper_mm_to_ft(6, form.scale_x), cy + geometry.paper_mm_to_ft(25, form.scale_x) 
                    
                    while any(abs(p_sh_x - px) < geometry.paper_mm_to_ft(35, form.scale_x) and abs(p_sh_y - py) < geometry.paper_mm_to_ft(10, form.scale_x) for px, py in placed_labels):
                        p_sh_y += geometry.paper_mm_to_ft(10, form.scale_x)
                            
                    placed_labels.append((p_sh_x, p_sh_y))
                    
                    if not profile_builder.create_leader_annotation(doc, new_view, DB.XYZ(p_sh_x, p_sh_y, 0), DB.XYZ(cx, cy, 0), sym_an, top_text, bot_text, calc_len_mm):
                        sh_len = geometry.paper_mm_to_ft(calc_len_mm, form.scale_x)
                        profile_builder.draw_line(doc, new_view, cx, cy, p_sh_x, p_sh_y, s_ord)
                        profile_builder.draw_line(doc, new_view, p_sh_x, p_sh_y, p_sh_x + sh_len, p_sh_y, s_ord)
                        profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), top_text, 0, txt_id, valign=DB.VerticalTextAlignment.Bottom)
                        profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), bot_text, 0, txt_id, valign=DB.VerticalTextAlignment.Top)
                except: pass

            # Выноски пересекаемых труб
            for cr in cross_pipes:
                try:
                    cx, cy = cr["x"], (cr["z"] - base_z) * DISTORTION_Y
                    real_d = cr.get("real_d_out", 0.1)
                    
                    # РАЗДЕЛЯЕМ ЛОГИКУ ОТРИСОВКИ:
                    if cr.get("in_manhole", False):
                        # Труба в колодце -> жесткий радиус 500мм (диаметр 1000мм)
                        # Это визуально совпадет с шириной колодца 1000мм.
                        R = 0.5 / 0.3048 
                    else:
                        # Труба в земле -> реальный радиус, умноженный на пользовательское искажение по вертикали (DISTORTION_Y)
                        # Чтобы на чертеже она была нужного размера и соответствовала вертикальным отметкам.
                        R = max((real_d / 2.0) * DISTORTION_Y, 0.005)
                        
                    s_pipe_cp = revit_utils.get_line_style(doc, form.selected_styles.get("sys_" + cr.get("abbr", "Система не задана"), "Тонкие линии")) or revit_utils.get_line_style(doc, "Тонкие линии")
                    
                    # Рисуем ровный круг из двух дуг
                    profile_builder.draw_arc(doc, new_view, DB.XYZ(cx-R, cy, 0), DB.XYZ(cx+R, cy, 0), DB.XYZ(cx, cy+R, 0), s_pipe_cp)
                    profile_builder.draw_arc(doc, new_view, DB.XYZ(cx+R, cy, 0), DB.XYZ(cx-R, cy, 0), DB.XYZ(cx, cy-R, 0), s_pipe_cp)
                    profile_builder.draw_line(doc, new_view, cx, cy - R, cx, y_lines[0], s_ord)
                    
                    # Формируем текст выноски (всегда использует реальный диаметр)
                    t_top = "{} {:.2f}".format(cr["abbr"], (cr["z"] - real_d/2.0) * 0.3048).strip().replace('.', ',')
                    t_bot = "Ø{}".format(int(round(real_d * 304.8)))
                    calc_len_mm = max(len(t_top), len(t_bot)) * 1.8 + 2.0
                    p_sh_x, p_sh_y = cx + geometry.paper_mm_to_ft(6, form.scale_x), cy - R - geometry.paper_mm_to_ft(8, form.scale_x)
                    
                    while any(abs(p_sh_x - px) < geometry.paper_mm_to_ft(35, form.scale_x) and abs(p_sh_y - py) < geometry.paper_mm_to_ft(10, form.scale_x) for px, py in placed_labels):
                        p_sh_y -= geometry.paper_mm_to_ft(10, form.scale_x)
                            
                    placed_labels.append((p_sh_x, p_sh_y))
                    
                    if not profile_builder.create_leader_annotation(doc, new_view, DB.XYZ(p_sh_x, p_sh_y, 0), DB.XYZ(cx, cy - R, 0), sym_an, t_top, t_bot, calc_len_mm):
                        sh_len = geometry.paper_mm_to_ft(calc_len_mm, form.scale_x)
                        profile_builder.draw_line(doc, new_view, cx, cy - R, p_sh_x, p_sh_y, s_ord)
                        profile_builder.draw_line(doc, new_view, p_sh_x, p_sh_y, p_sh_x + sh_len, p_sh_y, s_ord)
                        profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), t_top, 0, txt_id, valign=DB.VerticalTextAlignment.Bottom)
                        profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), t_bot, 0, txt_id, valign=DB.VerticalTextAlignment.Top)
                except: pass

            # Заполнение ячеек таблицы
            segments, desc_segments, base_segments = [], [], []
            for i in range(len(final_xs) - 1):
                x1, x2 = final_xs[i], final_xs[i+1]
                mid_x = (x1 + x2) / 2.0
                best_desc, best_base = "Труба", "Основание не задано H=100 мм"
                for d in raw_d:
                    if not d.get("is_vert", False) and min(d["x1"], d["x2"]) <= mid_x <= max(d["x1"], d["x2"]):
                        best_desc, best_base = d.get("desc", "Труба"), d.get("base_text", "Основание не задано H=100 мм")
                        break
                desc_segments.append({"x1": x1, "x2": x2, "desc": best_desc})
                base_segments.append({"x1": x1, "x2": x2, "base_text": best_base})
                z1 = geometry.get_horiz_pipe_z_center(x1, raw_d)
                z2 = geometry.get_horiz_pipe_z_center(x2, raw_d)
                segments.append({"x1": x1, "x2": x2, "L": float("{:.1f}".format((x2 - x1) * 0.3048)), "slope": (z2 - z1) * 0.3048 / ((x2 - x1) * 0.3048) * 1000.0 if (x2 - x1) > 0.001 else 0.0})

            # Обычная группировка для текстов (Обозначение трубы и Основание)
            def group_segs(segs, key):
                if not segs: return [], set()
                grouped, cur = [], segs[0].copy()
                for s in segs[1:]:
                    if s[key] == cur[key]:
                        cur["x2"] = s["x2"]
                    else: 
                        grouped.append(cur)
                        cur = s.copy()
                grouped.append(cur)
                return grouped, set([g["x1"] for g in grouped] + [g["x2"] for g in grouped])

            pipe_groups, pipe_boundaries = group_segs(desc_segments, "desc")
            base_groups, base_boundaries = group_segs(base_segments, "base_text")

            # --- УМНАЯ ЛОГИКА ГРУППИРОВКИ УКЛОНОВ ---
            # Объединяем, если направления совпадают И разница уклонов <= 5 промилле.
            # Также "проглатываем" нулевые уклоны (колодцы и вертикальные стыки).
            grouped_segments = []
            if segments:
                cur = segments[0].copy()
                cur["orig_slope"] = cur["slope"]
                for s in segments[1:]:
                    s_slope = s["slope"]
                    c_slope = cur["orig_slope"]
                    
                    is_zero_s = abs(s_slope) <= 0.5
                    is_zero_c = abs(c_slope) <= 0.5
                    
                    same_dir = geometry.get_dir(s_slope) == geometry.get_dir(c_slope)
                    slope_diff = abs(abs(s_slope) - abs(c_slope))
                    
                    # Условия слияния ячеек:
                    if is_zero_s or is_zero_c or (same_dir and slope_diff <= 5.0):
                        cur["x2"] = s["x2"]
                        cur["L"] = round(cur["L"] + s["L"], 1)
                        # Если текущий доминантный уклон был нулевым, перехватываем новый
                        if is_zero_c and not is_zero_s:
                            cur["orig_slope"] = s_slope
                    else:
                        # Разница больше 5 промилле - создаем отдельный участок!
                        grouped_segments.append(cur)
                        cur = s.copy()
                        cur["orig_slope"] = cur["slope"]
                grouped_segments.append(cur)
                
            slope_boundaries = set([g["x1"] for g in grouped_segments] + [g["x2"] for g in grouped_segments])

            for x in final_xs[1:-1]:
                if any(abs(x - bx) < 0.01 for bx in pipe_boundaries): profile_builder.draw_line(doc, new_view, x, y_lines[5], x, y_lines[6], s_grid)
                if any(abs(x - bx) < 0.01 for bx in base_boundaries): profile_builder.draw_line(doc, new_view, x, y_lines[6], x, y_lines[7], s_grid)
                if any(abs(x - bx) < 0.01 for bx in slope_boundaries): profile_builder.draw_line(doc, new_view, x, y_lines[7], x, y_lines[8], s_grid)
                profile_builder.draw_line(doc, new_view, x, y_lines[8], x, y_lines[10], s_grid)
                
            for g in pipe_groups: profile_builder.place_text(doc, new_view.Id, (g["x1"] + g["x2"]) / 2.0, (y_lines[5] + y_lines[6]) / 2.0, g["desc"], 0.0, txt_id)
            for g in base_groups: profile_builder.place_text(doc, new_view.Id, (g["x1"] + g["x2"]) / 2.0, (y_lines[6] + y_lines[7]) / 2.0, g["base_text"], 0.0, txt_id)

            picket_dists = [0.0]
            for i in range(len(final_xs) - 1): picket_dists.append(picket_dists[-1] + float("{:.1f}".format((final_xs[i+1] - final_xs[i]) * 0.3048)))
            
            for idx, x in enumerate(final_xs):
                z_b_val = geometry.get_z_on_profile(x, cln_b)
                z_r_val = geometry.get_z_on_profile(x, cln_r) if cln_r else z_b_val
                z_cen, d_val, cushion_m = geometry.get_exact_pipe_data(x, raw_d)
                
                z_top_text = round((z_cen + (d_val / 2.0)) * 0.3048 + 1e-9, 2)
                z_bot_text = round(z_top_text - round(d_val * 0.3048 + 1e-9, 3) - round(cushion_m, 3) + 1e-9, 2)
                
                z_b_str = "{:.2f}".format(round(z_b_val * 0.3048 + 1e-9, 2)).replace('.', ',') if z_b_val is not None else ""
                z_r_text = round(z_r_val * 0.3048 + 1e-9, 2) if z_r_val is not None else (round(z_b_val * 0.3048 + 1e-9, 2) if z_b_val else 0)
                z_r_str = "{:.2f}".format(z_r_text).replace('.', ',') if z_r_val is not None else ""
                
                depth_text = round(z_r_text - z_bot_text + 1e-9, 2)
                
                max_ground_z = max([v for v in [z_b_val, z_r_val] if v is not None] or [cln_b[-1]["z"]])
                profile_builder.draw_line(doc, new_view, x, y_lines[0], x, (max_ground_z - base_z) * DISTORTION_Y, s_ord)

                ang = math.pi / 2.0
                offset = geometry.paper_mm_to_ft(1.5, form.scale_x) 
                
                if z_r_str: profile_builder.place_text(doc, new_view.Id, x, (y_lines[0] + y_lines[1])/2.0, z_r_str, ang, txt_id)
                if z_b_str: profile_builder.place_text(doc, new_view.Id, x, (y_lines[1] + y_lines[2])/2.0, z_b_str, ang, txt_id)
                profile_builder.place_text(doc, new_view.Id, x, (y_lines[2] + y_lines[3])/2.0, "{:.2f}".format(z_bot_text).replace('.', ','), ang, txt_id)
                profile_builder.place_text(doc, new_view.Id, x, (y_lines[3] + y_lines[4])/2.0, "{:.2f}".format(z_top_text).replace('.', ','), ang, txt_id)
                profile_builder.place_text(doc, new_view.Id, x, (y_lines[4] + y_lines[5])/2.0, "{:.2f}".format(depth_text).replace('.', ','), ang, txt_id)
                profile_builder.place_text(doc, new_view.Id, x + offset, (y_lines[9] + y_lines[10])/2.0, "ПК{}+{:.1f}".format(int(picket_dists[idx] // 100), picket_dists[idx] % 100).replace('.', ','), ang, txt_id)

            for g in grouped_segments:
                x_start, x_end, L_m = g["x1"], g["x2"], g["L"]
                
                # ЧЕСТНЫЙ РАСЧЕТ УКЛОНА (как требует Нормоконтроль):
                # Вычисляем дельту строго по округленным отметкам, которые пишутся в таблицу (Отметка верха трубы)
                z1_cen, d1, _ = geometry.get_exact_pipe_data(x_start, raw_d)
                z2_cen, d2, _ = geometry.get_exact_pipe_data(x_end, raw_d)
                
                z1_text = round((z1_cen + (d1 / 2.0)) * 0.3048 + 1e-9, 2)
                z2_text = round((z2_cen + (d2 / 2.0)) * 0.3048 + 1e-9, 2)
                
                if L_m > 0.001:
                    calc_slope = (z2_text - z1_text) / L_m * 1000.0
                else:
                    calc_slope = 0.0
                    
                # Направление (вверх/вниз/прямо) берем из исходной геометрии (orig_slope), 
                # чтобы визуальная линия в таблице не сломалась из-за микро-погрешностей
                orig_slope = g.get("orig_slope", g["slope"])
                dir_val = geometry.get_dir(orig_slope)
                
                y_t, y_b, w = y_lines[7], y_lines[8], x_end - x_start
                txt_s, txt_L = geometry.fmt_slope(abs(calc_slope)), geometry.fmt_len(L_m)
                
                if dir_val < 0:
                    profile_builder.draw_line(doc, new_view, x_start, y_t, x_end, y_b, s_grid) 
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.75, y_t - (y_t-y_b)*0.25, txt_s, 0.0, txt_id)
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.25, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)
                elif dir_val > 0:
                    profile_builder.draw_line(doc, new_view, x_start, y_b, x_end, y_t, s_grid) 
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.25, y_t - (y_t-y_b)*0.25, txt_s, 0.0, txt_id)
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.75, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)
                else:
                    y_m = (y_t + y_b) / 2.0
                    profile_builder.draw_line(doc, new_view, x_start, y_m, x_end, y_m, s_grid) 
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.5, y_t - (y_t-y_b)*0.25, "0", 0.0, txt_id)
                    profile_builder.place_text(doc, new_view.Id, x_start + w*0.5, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)

            for i in range(len(final_xs) - 1):
                profile_builder.place_text(doc, new_view.Id, (final_xs[i] + final_xs[i+1]) / 2.0, (y_lines[8] + y_lines[9])/2.0, geometry.fmt_len(float("{:.1f}".format((final_xs[i+1] - final_xs[i]) * 0.3048))), 0.0, txt_id)

            forms.alert("Профиль успешно построен!\nВид: {}".format(new_view.Name), warn_icon=False)

        except Exception as e:
            print(traceback.format_exc())
            forms.alert("Критическая ошибка при построении:\n{}".format(str(e)), exitscript=True)

if __name__ == '__main__':
    main() 
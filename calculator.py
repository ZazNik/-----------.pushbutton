# -*- coding: utf-8 -*-
import math
from pyrevit import DB
import revit_utils
import geometry
from constants import *

class ProfileCalculator:
    def __init__(self, doc, form, selected_elements, start_element, main_pipes, casing_pipes, manholes, ordered_nodes, o_pipes):
        self.doc = doc
        self.form = form
        self.selected_elements = selected_elements
        self.start_element = start_element
        self.main_pipes = main_pipes
        self.casing_pipes = casing_pipes
        self.manholes = manholes
        self.ordered_nodes = ordered_nodes
        self.o_pipes = o_pipes

    def calculate(self):
        # Пробрасываем локальные переменные для перенесенного кода
        doc, form = self.doc, self.form
        selected_elements, start_element = self.selected_elements, self.start_element
        main_pipes, casing_pipes, manholes = self.main_pipes, self.casing_pipes, self.manholes
        ordered_nodes, o_pipes = self.ordered_nodes, self.o_pipes

        geom_opt = DB.Options()
        e_blk = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_blk)
        e_red = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_red) if form.selected_layer_red != "<Нет>" else []
        e_blk_bnd = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_blk + "_граница")
        e_red_bnd = revit_utils.get_edges_from_layer(doc, form.selected_dwg.get_Geometry(geom_opt), form.selected_layer_red + "_граница") if form.selected_layer_red != "<Нет>" else []
        
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
            
            type_comments, size_str, thick_str, base_str, h_val_str = "", "", "", DEF_BASE, "100"
            cushion_m = 0.1
            
            p_type_id = seg["pipe"].GetTypeId()
            if p_type_id != DB.ElementId.InvalidElementId:
                p_type = doc.GetElement(p_type_id)
                tc_param = p_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS)
                if tc_param and tc_param.HasValue: type_comments = tc_param.AsString()
                    
            size_p = seg["pipe"].LookupParameter(PRM_SIZE) or seg["pipe"].get_Parameter(DB.BuiltInParameter.RBS_CALCULATED_SIZE_STRING)
            if size_p and size_p.HasValue: size_str = size_p.AsString()
                
            thick_p = seg["pipe"].LookupParameter(PRM_THICKNESS) or (doc.GetElement(p_type_id).LookupParameter(PRM_THICKNESS) if p_type_id != DB.ElementId.InvalidElementId else None)
            if thick_p and thick_p.HasValue:
                if thick_p.StorageType == DB.StorageType.Double:
                    thick_str = "{:.1f}".format(thick_p.AsDouble() * 304.8).replace('.', ',').rstrip(',0')
                else: thick_str = thick_p.AsString() or thick_p.AsValueString()

            desc = u"{} {}x{}".format(type_comments, size_str, thick_str).strip() if thick_str else u"{} {}".format(type_comments, size_str).strip()
            if not desc: desc = DEF_DESC

            base_param = seg["pipe"].LookupParameter(PRM_BASE)
            if base_param and base_param.HasValue: base_str = base_param.AsString().replace('\\n', '\n')

            h_param = seg["pipe"].LookupParameter(PRM_BASE_H)
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
                if KW_CASING in t_name: continue
                    
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

        # Упаковываем всю вычисленную геометрию для передачи в рендерер
        return {
            "raw_d": raw_d,
            "p_geom": p_geom,
            "casings_geom": casings_geom,
            "cln_b": cln_b,
            "cln_r": cln_r,
            "manholes": manholes,
            "final_xs": final_xs,
            "cross_pipes": cross_pipes,
            "base_z": base_z,
            "base_z_m": base_z_m,
            "DISTORTION_Y": DISTORTION_Y,
            "cur_x": cur_x,
            "all_z": all_z
        }
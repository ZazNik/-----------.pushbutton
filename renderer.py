# -*- coding: utf-8 -*-
import math
import System
from pyrevit import DB, forms
import profile_builder
import geometry
import revit_utils
from constants import *

class ProfileRenderer:
    def __init__(self, doc, form, data, target_view=None, draw_sidebar=True):
        self.doc = doc
        self.form = form
        self.data = data
        self.target_view = target_view
        self.draw_sidebar = draw_sidebar

    def render(self):
        doc = self.doc
        form = self.form
        
        raw_d = self.data["raw_d"]
        p_geom = self.data["p_geom"]
        casings_geom = self.data["casings_geom"]
        cln_b = self.data["cln_b"]
        cln_r = self.data["cln_r"]
        manholes = self.data["manholes"]
        real_mhs = self.data.get("real_mhs", [])
        final_xs = self.data["final_xs"]
        cross_pipes = self.data["cross_pipes"]
        base_z = self.data["base_z"]
        base_z_m = self.data["base_z_m"]
        DISTORTION_Y = self.data["DISTORTION_Y"]
        start_x = self.data.get("start_x", 0.0)
        cur_x = self.data["cur_x"]
        all_z = self.data["all_z"]

        # --- ЧИСТЫЙ ТРЕКЕР (Без костылей) ---
        manual_deltas = self.data.get("manual_deltas", {})
        tracked_annotations = {}
        
        def track_and_apply_delta(element, key, math_x, math_y, math_lx=None, math_ly=None, is_text=False, is_line=False):
            if not element: return
            net_id = self.data.get("net_id", "Трасса_1")
            key = "{}_{}".format(net_id, key)
            try:
                d = manual_deltas.get(key)
                
                if is_text:
                    orig_w = element.Width
                    if d:
                        if "w" in d:
                            try: element.Width = d["w"]
                            except: pass
                        if "dx" in d or "dy" in d:
                            DB.ElementTransformUtils.MoveElement(doc, element.Id, DB.XYZ(d.get("dx",0), d.get("dy",0), 0))
                    # Пишем идеальную математику! (Сбросы дельт больше не страшны благодаря script.py)
                    tracked_annotations[key] = {"type": "text", "id": element.Id.IntegerValue, "x": math_x, "y": math_y, "w": orig_w}
                    
                elif is_line:
                    if d and ("dx" in d or "dy" in d):
                        DB.ElementTransformUtils.MoveElement(doc, element.Id, DB.XYZ(d.get("dx",0), d.get("dy",0), 0))
                    tracked_annotations[key] = {"type": "line", "id": element.Id.IntegerValue, "x": math_x, "y": math_y}
                    
                else:
                    if d and ("dx" in d or "dy" in d):
                        DB.ElementTransformUtils.MoveElement(doc, element.Id, DB.XYZ(d.get("dx",0), d.get("dy",0), 0))
                    if d and ("ldx" in d or "ldy" in d) and math_lx is not None and math_ly is not None:
                        try:
                            ldrs = None
                            if hasattr(element, "GetLeaders"): ldrs = element.GetLeaders()
                            elif hasattr(element, "get_Leaders"): ldrs = element.get_Leaders()
                            elif hasattr(element, "Leaders"): ldrs = element.Leaders
                            if ldrs and len(ldrs) > 0:
                                ldrs[0].End = DB.XYZ(math_lx + d.get("ldx", 0), math_ly + d.get("ldy", 0), 0)
                        except: pass
                    tracked_annotations[key] = {"type": "leader", "id": element.Id.IntegerValue, "x": math_x, "y": math_y, "lx": math_lx, "ly": math_ly}
            except: pass

        s_blk = revit_utils.get_line_style(doc, form.selected_styles.get("style_blk", DEF_LINE_STYLE))
        s_red = revit_utils.get_line_style(doc, form.selected_styles.get("style_red", DEF_LINE_STYLE))
        s_casing = revit_utils.get_line_style(doc, form.selected_styles.get("style_casing", DEF_LINE_STYLE))
        s_well = revit_utils.get_line_style(doc, form.selected_styles.get("style_manhole", DEF_LINE_STYLE))
        s_ord = revit_utils.get_line_style(doc, form.selected_styles.get("style_ord", DEF_LINE_STYLE))
        s_grid = revit_utils.get_line_style(doc, form.selected_styles.get("style_grid", DEF_LINE_STYLE))

        if self.target_view: new_view = self.target_view
        else: new_view = profile_builder.create_drafting_view(doc, form.view_name, form.scale_x)
            
        existing_ids = set([e.Id.IntegerValue for e in DB.FilteredElementCollector(doc, new_view.Id).ToElements()])

        mh_intervals = []
        for rm in real_mhs:
            el = rm["el"]
            real_x = rm["mx"]
            is_cover = False
            names = [el.Name.lower() if el.Name else ""]
            el_type = doc.GetElement(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None
            if el_type:
                if hasattr(el_type, "FamilyName") and el_type.FamilyName: names.append(el_type.FamilyName.lower())
                sym_name = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                if sym_name and sym_name.HasValue: names.append(sym_name.AsString().lower())
            if any(KW_COVER in n or KW_COVER_ALT in n for n in names): is_cover = True

            w_ft = 0.5 / 0.3048 
            rm["w"] = w_ft
            if not is_cover: mh_intervals.append((real_x - w_ft, real_x + w_ft))

        def extend_to_mhs(pt, other_pt):
            if abs(pt[0] - other_pt[0]) < 1e-3: return [pt[0], pt[1]]
            for rm in real_mhs:
                mx, w = rm["mx"], rm["w"]
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

        for p in p_geom:
            sys_key = "sys_" + p.get("abbr", DEF_SYSTEM)
            s_pipe_seg = revit_utils.get_line_style(doc, form.selected_styles.get(sys_key, DEF_LINE_STYLE)) or revit_utils.get_line_style(doc, DEF_LINE_STYLE)
            t0, t1 = extend_to_mhs(p["top"][0], p["top"][1]), extend_to_mhs(p["top"][1], p["top"][0])
            b0, b1 = extend_to_mhs(p["bot"][0], p["bot"][1]), extend_to_mhs(p["bot"][1], p["bot"][0])
            draw_clipped_line(t0[0], t0[1], t1[0], t1[1], s_pipe_seg)
            draw_clipped_line(b0[0], b0[1], b1[0], b1[1], s_pipe_seg)
            
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
                c_geom.append({"top": [[x1+dx_T, y1+dy_T], [x2+dx_T, y2+dy_T]], "bot": [[x1-dx_T, y1-dy_T], [x2-dx_T, y2-dy_T]], "cap_l": True, "cap_r": True, "pipe": c["pipe"]})
                
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

        def draw_ground(pts, style):
            for k in range(len(pts) - 1):
                x1, z1 = pts[k]["x"], pts[k]["z"]
                x2, z2 = pts[k+1]["x"], pts[k+1]["z"]
                if x2 < start_x - 1e-5 or x1 > cur_x + 1e-5: continue
                if x1 < start_x:
                    z1 = z1 + (z2 - z1) * (start_x - x1) / (x2 - x1) if x2 != x1 else z1
                    x1 = start_x
                if x2 > cur_x:
                    z2 = z1 + (z2 - z1) * (cur_x - x1) / (x2 - x1) if x2 != x1 else z2
                    x2 = cur_x
                profile_builder.draw_line(doc, new_view, x1, (z1-base_z)*DISTORTION_Y, x2, (z2-base_z)*DISTORTION_Y, style)

        draw_ground(cln_b, s_blk)
        if cln_r: draw_ground(cln_r, s_red)

        for rm in real_mhs:
            el = rm["el"]
            real_x = rm["mx"]
            bb = el.get_BoundingBox(None)
            if not bb: continue
            is_cover = False
            names = [el.Name.lower() if el.Name else ""]
            el_type = doc.GetElement(el.GetTypeId()) if el.GetTypeId() != DB.ElementId.InvalidElementId else None
            if el_type:
                if hasattr(el_type, "FamilyName") and el_type.FamilyName: names.append(el_type.FamilyName.lower())
                sym_name = el_type.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                if sym_name and sym_name.HasValue: names.append(sym_name.AsString().lower())
            if any(KW_COVER in n or KW_COVER_ALT in n for n in names): is_cover = True

            fixed_width = 1.0 / 0.3048
            if is_cover:
                z_top = bb.Max.Z
                z_bot = z_top - (0.2 / 0.3048) 
                profile_builder.draw_manhole(doc, new_view, real_x, fixed_width, z_bot, z_top, base_z, DISTORTION_Y, s_well)
            else:
                profile_builder.draw_manhole(doc, new_view, real_x, fixed_width, bb.Min.Z, bb.Max.Z, base_z, DISTORTION_Y, s_well)

        # ==========================================
        # РАСЧЕТ ДАННЫХ ДЛЯ ТАБЛИЦЫ
        # ==========================================
        segments, desc_segments, base_segments = [], [], []
        for i in range(len(final_xs) - 1):
            x1, x2 = final_xs[i], final_xs[i+1]
            mid_x = (x1 + x2) / 2.0
            best_desc, best_base = DEF_DESC, "Основание не задано H=100 мм"
            best_pipe = None
            
            for d in raw_d:
                if not d.get("is_vert", False) and min(d["x1"], d["x2"]) <= mid_x <= max(d["x1"], d["x2"]):
                    best_desc, best_base = d.get("desc", DEF_DESC), d.get("base_text", "Основание не задано H=100 мм")
                    best_pipe = d
                    break
                    
            desc_segments.append({"x1": x1, "x2": x2, "desc": best_desc})
            base_segments.append({"x1": x1, "x2": x2, "base_text": best_base})
            
            L_m = float("{:.1f}".format((x2 - x1) * 0.3048))
            
            if best_pipe:
                px1, pz1, px2, pz2 = best_pipe["x1"], best_pipe["z1"], best_pipe["x2"], best_pipe["z2"]
                d_out = best_pipe["d_outer"]
                if abs(px2 - px1) > 1e-6:
                    z_cen_1 = pz1 + (x1 - px1) * (pz2 - pz1) / (px2 - px1)
                    z_cen_2 = pz1 + (x2 - px1) * (pz2 - pz1) / (px2 - px1)
                else:
                    z_cen_1 = z_cen_2 = (pz1 + pz2) / 2.0
                    
                z_bot_1 = round((z_cen_1 - (d_out / 2.0)) * 0.3048 + 1e-9, 2)
                z_bot_2 = round((z_cen_2 - (d_out / 2.0)) * 0.3048 + 1e-9, 2)
                calc_slope = (z_bot_2 - z_bot_1) / L_m * 1000.0 if L_m > 0.001 else 0.0
            else:
                calc_slope = 0.0
            segments.append({"x1": x1, "x2": x2, "L": L_m, "slope": calc_slope})

        def group_segs(segs, key):
            if not segs: return [], set()
            grouped, cur = [], segs[0].copy()
            for s in segs[1:]:
                if s[key] == cur[key]: cur["x2"] = s["x2"]
                else: 
                    grouped.append(cur)
                    cur = s.copy()
            grouped.append(cur)
            return grouped, set([g["x1"] for g in grouped] + [g["x2"] for g in grouped])

        pipe_groups, pipe_boundaries = group_segs(desc_segments, "desc")
        base_groups, base_boundaries = group_segs(base_segments, "base_text")

        tolerance = getattr(form, 'slope_tol_val', 0.5)
        grouped_segments = []
        if segments:
            cur = segments[0].copy()
            cur["orig_slope"] = cur["slope"]
            for s in segments[1:]:
                dir_cur = geometry.get_dir(cur["slope"])
                dir_s = geometry.get_dir(s["slope"])
                x_bnd = s["x1"]
                inc_bots = [d["z2"] - (d["d_outer"] / 2.0) for d in raw_d if not d.get("is_vert", False) and abs(d["x2"] - x_bnd) < 0.01]
                out_bots = [d["z1"] - (d["d_outer"] / 2.0) for d in raw_d if not d.get("is_vert", False) and abs(d["x1"] - x_bnd) < 0.01]
                has_drop = False
                if inc_bots and out_bots:
                    z_in_m = round(inc_bots[0] * 0.3048 + 1e-9, 2)
                    z_out_m = round(out_bots[0] * 0.3048 + 1e-9, 2)
                    if "{:.2f}".format(z_in_m) != "{:.2f}".format(z_out_m):
                        has_drop = True
                
                if dir_cur == dir_s and abs(cur["slope"] - s["slope"]) <= tolerance and not has_drop:
                    prev_L = cur["L"]
                    cur["x2"] = s["x2"]
                    cur["L"] = float("{:.1f}".format(prev_L + s["L"]))
                    if cur["L"] > 0.001: cur["slope"] = (cur["slope"] * prev_L + s["slope"] * s["L"]) / cur["L"]
                else:
                    grouped_segments.append(cur)
                    cur = s.copy()
                    cur["orig_slope"] = cur["slope"]
            grouped_segments.append(cur)
            
        slope_boundaries = set([g["x1"] for g in grouped_segments] + [g["x2"] for g in grouped_segments])

        # ==========================================
        # 8. ОТРИСОВКА ПОДВАЛА (Сетка и Тексты)
        # ==========================================
        txt_type = next((t for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType)), None)
        txt_id = txt_type.Id if txt_type else DB.ElementId.InvalidElementId
        
        row_heights_mm = [15, 15, 15, 15, 10, 10, 10, 15]
        labels = ["Отметка низа трубы, м", "Отметка земли проектная, м", "Отметка земли фактическая, м", "Обозначение трубы и тип изоляции", "Основание", "Уклон, ‰ / Длина, м", "Расстояние, м", "Номер колодца"]       
        y_lines = [0.0] 
        for h in row_heights_mm: y_lines.append(y_lines[-1] - geometry.paper_mm_to_ft(h, form.scale_x))
        
        x_data_start = final_xs[0]
        x_data_end = final_xs[-1]
        
        x_table_start = start_x
        # 5-метровый хвост таблицы (чтобы не было разрыва)
        x_table_end = cur_x + (5.0 / 0.3048)
        
        # Увеличенный зазор (25мм), чтобы боковик изначально не налезал на ординаты
        W_gap = geometry.paper_mm_to_ft(25, form.scale_x)     
        W_col_left = geometry.paper_mm_to_ft(60, form.scale_x) 

        net_id = self.data.get("net_id", "Трасса_1")
        left_dx = manual_deltas.get("{}_sb_vline_l".format(net_id), {}).get("dx", 0.0)
        right_dx = manual_deltas.get("{}_sb_vline_r".format(net_id), {}).get("dx", 0.0)
        
        x_labels_right = x_table_start - W_gap
        x_labels_left = x_labels_right - W_col_left
        
        x_grid_start = (x_labels_left + left_dx) if self.draw_sidebar else x_table_start - (35.0 / 0.3048)

        if self.draw_sidebar:
            x_arr = x_labels_right - geometry.paper_mm_to_ft(12, form.scale_x)
            y_arr = y_lines[0]
            h_stem = geometry.paper_mm_to_ft(6, form.scale_x)
            w_line = geometry.paper_mm_to_ft(14, form.scale_x)
            dx_arr = geometry.paper_mm_to_ft(1.0, form.scale_x)
            dy_arr = geometry.paper_mm_to_ft(2.5, form.scale_x)
            
            l1 = profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr, y_arr + h_stem, s_grid) 
            track_and_apply_delta(l1, "sb_arr1", x_arr, y_arr, is_line=True)
            l2 = profile_builder.draw_line(doc, new_view, x_arr, y_arr + h_stem, x_arr - w_line, y_arr + h_stem, s_grid) 
            track_and_apply_delta(l2, "sb_arr2", x_arr, y_arr + h_stem, is_line=True)
            l3 = profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr - dx_arr, y_arr + dy_arr, s_grid)
            track_and_apply_delta(l3, "sb_arr3", x_arr, y_arr, is_line=True)
            l4 = profile_builder.draw_line(doc, new_view, x_arr, y_arr, x_arr + dx_arr, y_arr + dy_arr, s_grid)
            track_and_apply_delta(l4, "sb_arr4", x_arr, y_arr, is_line=True)
            
            tx_x, tx_y = x_arr - w_line/2.0, y_arr + h_stem + geometry.paper_mm_to_ft(2, form.scale_x)
            tn = profile_builder.place_text(doc, new_view.Id, tx_x, tx_y, "{:.2f}".format(base_z_m).replace('.', ','), 0.0, txt_id)
            track_and_apply_delta(tn, "sb_bz", tx_x, tx_y, is_text=True)
            
            offset_x = x_labels_left + geometry.paper_mm_to_ft(2, form.scale_x) 
            tx_y1, tx_y2, tx_y3 = y_arr + geometry.paper_mm_to_ft(20, form.scale_x), y_arr + geometry.paper_mm_to_ft(15, form.scale_x), y_arr + geometry.paper_mm_to_ft(10, form.scale_x)

            tn1 = profile_builder.place_text(doc, new_view.Id, offset_x, tx_y1, "Масштабы:", 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left)
            track_and_apply_delta(tn1, "sb_sc1", offset_x, tx_y1, is_text=True)
            tn2 = profile_builder.place_text(doc, new_view.Id, offset_x, tx_y2, "горизонтальный 1:{}".format(form.scale_x), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left)
            track_and_apply_delta(tn2, "sb_sc2", offset_x, tx_y2, is_text=True)
            tn3 = profile_builder.place_text(doc, new_view.Id, offset_x, tx_y3, "вертикальный   1:{}".format(form.scale_y), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left)
            track_and_apply_delta(tn3, "sb_sc3", offset_x, tx_y3, is_text=True)

            for i, lab in enumerate(labels):
                if i == 5: 
                    actual_l = x_labels_left + left_dx
                    actual_r = x_labels_right + right_dx
                    profile_builder.draw_line(doc, new_view, actual_l, y_lines[i], actual_r, y_lines[i+1], s_grid)
                    
                    tx_x1, tx_y1 = x_labels_right - geometry.paper_mm_to_ft(2, form.scale_x), y_lines[i] - geometry.paper_mm_to_ft(1.5, form.scale_x)
                    tn1 = profile_builder.place_text(doc, new_view.Id, tx_x1, tx_y1, "Уклон, ‰", 0.0, txt_id, halign=DB.HorizontalTextAlignment.Right, valign=DB.VerticalTextAlignment.Top)
                    track_and_apply_delta(tn1, "sb_l5_top", tx_x1, tx_y1, is_text=True)
                    
                    tx_x2, tx_y2 = x_labels_left + geometry.paper_mm_to_ft(2, form.scale_x), y_lines[i+1] + geometry.paper_mm_to_ft(1.5, form.scale_x)
                    tn2 = profile_builder.place_text(doc, new_view.Id, tx_x2, tx_y2, "Длина, м", 0.0, txt_id, halign=DB.HorizontalTextAlignment.Left, valign=DB.VerticalTextAlignment.Bottom)
                    track_and_apply_delta(tn2, "sb_l5_bot", tx_x2, tx_y2, is_text=True)
                else:
                    x_m, y_m = (x_labels_left + x_labels_right) / 2.0, (y_lines[i] + y_lines[i+1]) / 2.0
                    tn = profile_builder.place_text(doc, new_view.Id, x_m, y_m, lab, 0.0, txt_id)
                    track_and_apply_delta(tn, "sb_lab_{}".format(i), x_m, y_m, is_text=True)

        for i, y in enumerate(y_lines): 
            # БЕЗ ТРЕКЕРОВ! Линии резиново натягиваются от сдвинутого боковика до конца трассы
            profile_builder.draw_line(doc, new_view, x_grid_start, y, x_table_end, y, s_grid)
        
        if self.draw_sidebar:
            l_v1 = profile_builder.draw_line(doc, new_view, x_labels_left, y_lines[0], x_labels_left, y_lines[-1], s_grid) 
            track_and_apply_delta(l_v1, "sb_vline_l", x_labels_left, y_lines[0], is_line=True)
            l_v2 = profile_builder.draw_line(doc, new_view, x_labels_right, y_lines[0], x_labels_right, y_lines[-1], s_grid) 
            track_and_apply_delta(l_v2, "sb_vline_r", x_labels_right, y_lines[0], is_line=True)
            
        l_v3 = profile_builder.draw_line(doc, new_view, x_table_end, y_lines[0], x_table_end, y_lines[-1], s_grid) 
        track_and_apply_delta(l_v3, "sb_vline_end", x_table_end, y_lines[0], is_line=True)
        
        y_bot_start = y_lines[7]
        l_v4 = profile_builder.draw_line(doc, new_view, x_table_start, y_lines[5], x_table_start, y_bot_start, s_grid)
        track_and_apply_delta(l_v4, "sb_vline_ds", x_table_start, y_lines[5], is_line=True)
        
        y_bot_end = y_lines[7]
        l_v5 = profile_builder.draw_line(doc, new_view, cur_x, y_lines[5], cur_x, y_bot_end, s_grid)
        track_and_apply_delta(l_v5, "sb_vline_de", cur_x, y_lines[5], is_line=True)

        if self.draw_sidebar:
            y_scale_top = ((int(math.ceil(max(list(all_z) + ([p["z"] for p in cln_r] if cln_r else [])) * 0.3048)) + 1) / 0.3048 - base_z) * DISTORTION_Y
            l_sc = profile_builder.draw_line(doc, new_view, x_labels_right, y_lines[0], x_labels_right, y_scale_top, s_grid)
            track_and_apply_delta(l_sc, "sb_scale_v", x_labels_right, y_lines[0], is_line=True)
            tick_len = geometry.paper_mm_to_ft(2, form.scale_x)
            for z_m in range(int(math.floor(base_z_m)), int(math.ceil(max(list(all_z) + ([p["z"] for p in cln_r] if cln_r else [])) * 0.3048)) + 2):
                if z_m <= base_z_m + 0.01: continue
                y_tick = ((float(z_m) / 0.3048) - base_z) * DISTORTION_Y
                l_tk = profile_builder.draw_line(doc, new_view, x_labels_right, y_tick, x_labels_right - tick_len, y_tick, s_grid)
                track_and_apply_delta(l_tk, "sb_tick_{}".format(z_m), x_labels_right, y_tick, is_line=True)
                tx_x = x_labels_right - tick_len - geometry.paper_mm_to_ft(1, form.scale_x)
                tn = profile_builder.place_text(doc, new_view.Id, tx_x, y_tick, "{:.2f}".format(float(z_m)).replace('.', ','), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Right)
                track_and_apply_delta(tn, "sb_z_{}".format(z_m), tx_x, y_tick, is_text=True)

        # Вертикальные границы ячеек внутри таблицы
        for idx, x in enumerate(final_xs[1:-1]):
            if any(abs(x - bx) < 0.01 for bx in pipe_boundaries): 
                l = profile_builder.draw_line(doc, new_view, x, y_lines[3], x, y_lines[4], s_grid)
                track_and_apply_delta(l, "cell_v1_{}".format(idx), x, y_lines[3], is_line=True)
            if any(abs(x - bx) < 0.01 for bx in base_boundaries): 
                l = profile_builder.draw_line(doc, new_view, x, y_lines[4], x, y_lines[5], s_grid)
                track_and_apply_delta(l, "cell_v2_{}".format(idx), x, y_lines[4], is_line=True)
            if any(abs(x - bx) < 0.01 for bx in slope_boundaries): 
                l = profile_builder.draw_line(doc, new_view, x, y_lines[5], x, y_lines[6], s_grid)
                track_and_apply_delta(l, "cell_v3_{}".format(idx), x, y_lines[5], is_line=True)
            l = profile_builder.draw_line(doc, new_view, x, y_lines[6], x, y_lines[7], s_grid)
            track_and_apply_delta(l, "cell_v4_{}".format(idx), x, y_lines[6], is_line=True)

        # Тексты ячеек
        for idx, g in enumerate(pipe_groups): 
            x_m, y_m = (g["x1"] + g["x2"]) / 2.0, (y_lines[3] + y_lines[4]) / 2.0
            tn = profile_builder.place_text(doc, new_view.Id, x_m, y_m, g["desc"], 0.0, txt_id)
            track_and_apply_delta(tn, "desc_{}".format(idx), x_m, y_m, is_text=True)
            
        for idx, g in enumerate(base_groups): 
            x_m, y_m = (g["x1"] + g["x2"]) / 2.0, (y_lines[4] + y_lines[5]) / 2.0
            tn = profile_builder.place_text(doc, new_view.Id, x_m, y_m, g["base_text"], 0.0, txt_id)
            track_and_apply_delta(tn, "base_{}".format(idx), x_m, y_m, is_text=True)

        for idx, x in enumerate(final_xs):
            z_b_val = geometry.get_z_on_profile(x, cln_b)
            z_r_val = geometry.get_z_on_profile(x, cln_r) if cln_r else z_b_val
            for rm in real_mhs:
                if abs(x - rm["mx"]) < 0.01:
                    if rm.get("z_b") is not None: z_b_val = rm["z_b"]
                    if rm.get("z_r") is not None: z_r_val = rm["z_r"]
                    break
                    
            z_cen, d_val = geometry.get_exact_pipe_data(x, raw_d)
            inc_bots = [d["z2"] - (d["d_outer"] / 2.0) for d in raw_d if not d.get("is_vert", False) and abs(d["x2"] - x) < 0.01]
            out_bots = [d["z1"] - (d["d_outer"] / 2.0) for d in raw_d if not d.get("is_vert", False) and abs(d["x1"] - x) < 0.01]
            
            z_in_m = round(inc_bots[0] * 0.3048 + 1e-9, 2) if inc_bots else None
            z_out_m = round(out_bots[0] * 0.3048 + 1e-9, 2) if out_bots else None
            avail_bots = [b for b in [z_in_m, z_out_m] if b is not None]
            main_bot_m = min(avail_bots) if avail_bots else round((z_cen - (d_val / 2.0)) * 0.3048 + 1e-9, 2)
            
            z_b_str = "{:.2f}".format(round(z_b_val * 0.3048 + 1e-9, 2)).replace('.', ',') if z_b_val is not None else ""
            z_r_text = round(z_r_val * 0.3048 + 1e-9, 2) if z_r_val is not None else (round(z_b_val * 0.3048 + 1e-9, 2) if z_b_val else 0)
            z_r_str = "{:.2f}".format(z_r_text).replace('.', ',') if z_r_val is not None else ""
            
            max_ground_z = max([v for v in [z_b_val, z_r_val] if v is not None] or [cln_b[-1]["z"]])
            l_ord = profile_builder.draw_line(doc, new_view, x, y_lines[0], x, (max_ground_z - base_z) * DISTORTION_Y, s_ord)
            track_and_apply_delta(l_ord, "ord_{}".format(idx), x, y_lines[0], is_line=True)

            is_mh_ordinate = any(abs(x - rm["mx"]) < 0.01 for rm in real_mhs)
            if is_mh_ordinate:
                depth_h = z_r_text - main_bot_m
                y_ground = (z_r_val - base_z) * DISTORTION_Y
                y_text = y_ground + geometry.paper_mm_to_ft(2.0, form.scale_x)
                tn = profile_builder.place_text(doc, new_view.Id, x, y_text, "{:.2f}".format(depth_h).replace('.', ','), 0.0, txt_id, halign=DB.HorizontalTextAlignment.Center, valign=DB.VerticalTextAlignment.Bottom)
                track_and_apply_delta(tn, "depth_{}".format(idx), x, y_text, is_text=True)

            ang = math.pi / 2.0
            offset = geometry.paper_mm_to_ft(1.5, form.scale_x) 
            
            y_pipe_row_center = (y_lines[0] + y_lines[1]) / 2.0
            if z_in_m is not None and z_out_m is not None and abs(z_in_m - z_out_m) >= 0.01:
                str_in = "{:.2f}".format(z_in_m).replace('.', ',')
                str_out = "{:.2f}".format(z_out_m).replace('.', ',')
                tn1 = profile_builder.place_text(doc, new_view.Id, x - offset, y_pipe_row_center, str_in, ang, txt_id)
                track_and_apply_delta(tn1, "bot_in_{}".format(idx), x - offset, y_pipe_row_center, is_text=True)
                tn2 = profile_builder.place_text(doc, new_view.Id, x + offset, y_pipe_row_center, str_out, ang, txt_id)
                track_and_apply_delta(tn2, "bot_out_{}".format(idx), x + offset, y_pipe_row_center, is_text=True)
            else:
                single_bot = z_in_m if z_in_m is not None else (z_out_m if z_out_m is not None else main_bot_m)
                str_single = "{:.2f}".format(single_bot).replace('.', ',')
                tn = profile_builder.place_text(doc, new_view.Id, x, y_pipe_row_center, str_single, ang, txt_id)
                track_and_apply_delta(tn, "bot_sngl_{}".format(idx), x, y_pipe_row_center, is_text=True)
                
            if z_r_str: 
                tn = profile_builder.place_text(doc, new_view.Id, x, (y_lines[1] + y_lines[2])/2.0, z_r_str, ang, txt_id)
                track_and_apply_delta(tn, "zr_{}".format(idx), x, (y_lines[1] + y_lines[2])/2.0, is_text=True)
            if z_b_str: 
                tn = profile_builder.place_text(doc, new_view.Id, x, (y_lines[2] + y_lines[3])/2.0, z_b_str, ang, txt_id)
                track_and_apply_delta(tn, "zb_{}".format(idx), x, (y_lines[2] + y_lines[3])/2.0, is_text=True)

        for idx, g in enumerate(grouped_segments):
            x_start, x_end, L_m = g["x1"], g["x2"], g["L"]
            calc_slope = g["slope"]
            orig_slope = g.get("orig_slope", g["slope"])
            dir_val = geometry.get_dir(orig_slope)
            
            y_t, y_b, w = y_lines[5], y_lines[6], x_end - x_start
            txt_s, txt_L = geometry.fmt_slope(abs(calc_slope)), geometry.fmt_len(L_m)
            
            if dir_val < 0:
                l = profile_builder.draw_line(doc, new_view, x_start, y_t, x_end, y_b, s_grid) 
                track_and_apply_delta(l, "slp_l_{}".format(idx), x_start, y_t, is_line=True)
                t1 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.75, y_t - (y_t-y_b)*0.25, txt_s, 0.0, txt_id)
                track_and_apply_delta(t1, "slp_ts_{}".format(idx), x_start + w*0.75, y_t - (y_t-y_b)*0.25, is_text=True)
                t2 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.25, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)
                track_and_apply_delta(t2, "slp_tl_{}".format(idx), x_start + w*0.25, y_b + (y_t-y_b)*0.25, is_text=True)
            elif dir_val > 0:
                l = profile_builder.draw_line(doc, new_view, x_start, y_b, x_end, y_t, s_grid) 
                track_and_apply_delta(l, "slp_l_{}".format(idx), x_start, y_b, is_line=True)
                t1 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.25, y_t - (y_t-y_b)*0.25, txt_s, 0.0, txt_id)
                track_and_apply_delta(t1, "slp_ts_{}".format(idx), x_start + w*0.25, y_t - (y_t-y_b)*0.25, is_text=True)
                t2 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.75, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)
                track_and_apply_delta(t2, "slp_tl_{}".format(idx), x_start + w*0.75, y_b + (y_t-y_b)*0.25, is_text=True)
            else:
                y_m = (y_t + y_b) / 2.0
                l = profile_builder.draw_line(doc, new_view, x_start, y_m, x_end, y_m, s_grid) 
                track_and_apply_delta(l, "slp_l_{}".format(idx), x_start, y_m, is_line=True)
                t1 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.5, y_t - (y_t-y_b)*0.25, "0", 0.0, txt_id)
                track_and_apply_delta(t1, "slp_ts_{}".format(idx), x_start + w*0.5, y_t - (y_t-y_b)*0.25, is_text=True)
                t2 = profile_builder.place_text(doc, new_view.Id, x_start + w*0.5, y_b + (y_t-y_b)*0.25, txt_L, 0.0, txt_id)
                track_and_apply_delta(t2, "slp_tl_{}".format(idx), x_start + w*0.5, y_b + (y_t-y_b)*0.25, is_text=True)

        for i in range(len(final_xs) - 1):
            x_m, y_m = (final_xs[i] + final_xs[i+1]) / 2.0, (y_lines[6] + y_lines[7])/2.0
            tn = profile_builder.place_text(doc, new_view.Id, x_m, y_m, geometry.fmt_len(float("{:.1f}".format((final_xs[i+1] - final_xs[i]) * 0.3048))), 0.0, txt_id)
            track_and_apply_delta(tn, "dist_{}".format(i), x_m, y_m, is_text=True)

        param_guid = System.Guid(PRM_MH_GUID)
        for idx, rm in enumerate(real_mhs):
            el = rm["el"]
            real_x = rm["mx"] 
            p_num = el.get_Parameter(param_guid)
            if p_num and p_num.HasValue:
                mh_number = p_num.AsString() or p_num.AsValueString()
                if mh_number:
                    y_center = (y_lines[7] + y_lines[8]) / 2.0
                    tn = profile_builder.place_text(doc, new_view.Id, real_x, y_center, mh_number, 0.0, txt_id)
                    track_and_apply_delta(tn, "mh_num_{}".format(idx), real_x, y_center, is_text=True)

        sym_an = None
        for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_GenericAnnotation).ToElements():
            try:
                if FAM_LEADER_TEXT in (s.FamilyName.lower() if s.FamilyName else "") and FAM_LEADER_TEXT in (s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString().lower() if s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM) else ""):
                    sym_an = s; break
            except: pass

        placed_labels = []
        for c in casings_geom:
            try:
                cp = c["pipe"]
                cx, cy = (c["x1"] + c["x2"]) / 2.0, ((c["z1"] + c["z2"]) / 2.0 - base_z) * DISTORTION_Y
                c_type = doc.GetElement(cp.GetTypeId()) if cp.GetTypeId() != DB.ElementId.InvalidElementId else None
                top_text = (c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).AsString().strip() if c_type and c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS) and c_type.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).HasValue else KW_CASING) or KW_CASING
                size_p = cp.LookupParameter(PRM_SIZE) or cp.get_Parameter(DB.BuiltInParameter.RBS_CALCULATED_SIZE_STRING)
                size_str = size_p.AsString() if size_p and size_p.HasValue else ""
                thick_p = cp.LookupParameter(PRM_THICKNESS) or (doc.GetElement(cp.GetTypeId()).LookupParameter(PRM_THICKNESS) if cp.GetTypeId() != DB.ElementId.InvalidElementId else None)
                thick_str = "{:.1f}".format(thick_p.AsDouble() * 304.8).replace('.', ',').rstrip(',0') if thick_p and thick_p.HasValue and thick_p.StorageType == DB.StorageType.Double else (thick_p.AsString() or thick_p.AsValueString() if thick_p else "")
                len_p = cp.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
                len_str = "{:.1f}".format((len_p.AsDouble() * 0.3048) if len_p and len_p.HasValue else 0.0).replace('.', ',').rstrip(',0')
                bot_text = u"{}x{}, L={} м".format(size_str, thick_str, len_str) if thick_str else u"{}, L={} м".format(size_str, len_str)
                calc_len_mm = max(len(top_text), len(bot_text)) * 1.8 + 2.0
                p_sh_x, p_sh_y = cx + geometry.paper_mm_to_ft(6, form.scale_x), cy + geometry.paper_mm_to_ft(25, form.scale_x) 
                while any(abs(p_sh_x - px) < geometry.paper_mm_to_ft(35, form.scale_x) and abs(p_sh_y - py) < geometry.paper_mm_to_ft(10, form.scale_x) for px, py in placed_labels):
                    p_sh_y += geometry.paper_mm_to_ft(10, form.scale_x)
                placed_labels.append((p_sh_x, p_sh_y))
                leader = profile_builder.create_leader_annotation(doc, new_view, DB.XYZ(p_sh_x, p_sh_y, 0), DB.XYZ(cx, cy, 0), sym_an, top_text, bot_text, calc_len_mm)
                track_and_apply_delta(leader, "casing_{}".format(cp.Id.IntegerValue), p_sh_x, p_sh_y, cx, cy, is_text=False)
                if not leader:
                    sh_len = geometry.paper_mm_to_ft(calc_len_mm, form.scale_x)
                    l1 = profile_builder.draw_line(doc, new_view, cx, cy, p_sh_x, p_sh_y, s_ord)
                    track_and_apply_delta(l1, "casing_l1_{}".format(cp.Id.IntegerValue), cx, cy, is_line=True)
                    l2 = profile_builder.draw_line(doc, new_view, p_sh_x, p_sh_y, p_sh_x + sh_len, p_sh_y, s_ord)
                    track_and_apply_delta(l2, "casing_l2_{}".format(cp.Id.IntegerValue), p_sh_x, p_sh_y, is_line=True)
                    t1 = profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), top_text, 0, txt_id, valign=DB.VerticalTextAlignment.Bottom)
                    track_and_apply_delta(t1, "casing_t1_{}".format(cp.Id.IntegerValue), p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), is_text=True)
                    t2 = profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), bot_text, 0, txt_id, valign=DB.VerticalTextAlignment.Top)
                    track_and_apply_delta(t2, "casing_t2_{}".format(cp.Id.IntegerValue), p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), is_text=True)
            except: pass

        for cr in cross_pipes:
            try:
                cx, cy = cr["x"], (cr["z"] - base_z) * DISTORTION_Y
                real_d = cr.get("real_d_out", 0.1)
                if cr.get("in_manhole", False): R = 0.5 / 0.3048 
                else: R = max((real_d / 2.0) * DISTORTION_Y, 0.005)
                s_pipe_cp = revit_utils.get_line_style(doc, form.selected_styles.get("sys_" + cr.get("abbr", DEF_SYSTEM), DEF_LINE_STYLE)) or revit_utils.get_line_style(doc, DEF_LINE_STYLE)
                profile_builder.draw_arc(doc, new_view, DB.XYZ(cx-R, cy, 0), DB.XYZ(cx+R, cy, 0), DB.XYZ(cx, cy+R, 0), s_pipe_cp)
                profile_builder.draw_arc(doc, new_view, DB.XYZ(cx+R, cy, 0), DB.XYZ(cx-R, cy, 0), DB.XYZ(cx, cy-R, 0), s_pipe_cp)
                l_ord = profile_builder.draw_line(doc, new_view, cx, cy - R, cx, y_lines[0], s_ord)
                track_and_apply_delta(l_ord, "cross_ord_{}".format(cr.get("id", 0)), cx, cy - R, is_line=True)
                t_top = "{} {:.2f}".format(cr["abbr"], (cr["z"] - real_d/2.0) * 0.3048).strip().replace('.', ',')
                t_bot = "Ø{}".format(int(round(real_d * 304.8)))
                calc_len_mm = max(len(t_top), len(t_bot)) * 1.8 + 2.0
                p_sh_x, p_sh_y = cx + geometry.paper_mm_to_ft(6, form.scale_x), cy - R - geometry.paper_mm_to_ft(8, form.scale_x)
                while any(abs(p_sh_x - px) < geometry.paper_mm_to_ft(35, form.scale_x) and abs(p_sh_y - py) < geometry.paper_mm_to_ft(10, form.scale_x) for px, py in placed_labels):
                    p_sh_y -= geometry.paper_mm_to_ft(10, form.scale_x)
                placed_labels.append((p_sh_x, p_sh_y))
                leader = profile_builder.create_leader_annotation(doc, new_view, DB.XYZ(p_sh_x, p_sh_y, 0), DB.XYZ(cx, cy - R, 0), sym_an, t_top, t_bot, calc_len_mm)
                track_and_apply_delta(leader, "cross_{}".format(cr.get("id", 0)), p_sh_x, p_sh_y, cx, cy - R, is_text=False)
                if not leader:
                    sh_len = geometry.paper_mm_to_ft(calc_len_mm, form.scale_x)
                    l1 = profile_builder.draw_line(doc, new_view, cx, cy - R, p_sh_x, p_sh_y, s_ord)
                    track_and_apply_delta(l1, "cross_l1_{}".format(cr.get("id", 0)), cx, cy - R, is_line=True)
                    l2 = profile_builder.draw_line(doc, new_view, p_sh_x, p_sh_y, p_sh_x + sh_len, p_sh_y, s_ord)
                    track_and_apply_delta(l2, "cross_l2_{}".format(cr.get("id", 0)), p_sh_x, p_sh_y, is_line=True)
                    t1 = profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), t_top, 0, txt_id, valign=DB.VerticalTextAlignment.Bottom)
                    track_and_apply_delta(t1, "cross_t1_{}".format(cr.get("id", 0)), p_sh_x + sh_len/2.0, p_sh_y + geometry.paper_mm_to_ft(1.0, form.scale_x), is_text=True)
                    t2 = profile_builder.place_text(doc, new_view.Id, p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), t_bot, 0, txt_id, valign=DB.VerticalTextAlignment.Top)
                    track_and_apply_delta(t2, "cross_t2_{}".format(cr.get("id", 0)), p_sh_x + sh_len/2.0, p_sh_y - geometry.paper_mm_to_ft(1.0, form.scale_x), is_text=True)
            except: pass

        doc.Regenerate()
        current_ids = set([e.Id.IntegerValue for e in DB.FilteredElementCollector(doc, new_view.Id).ToElements()])
        generated_elements = list(current_ids - existing_ids)

        return new_view, generated_elements, tracked_annotations
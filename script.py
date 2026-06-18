# -*- coding: utf-8 -*-
__title__ = "Профиль\nГОСТ НВК"
__doc__ = "Создает продольный профиль наружных сетей (НВК) по ГОСТ на основе выделенных элементов и DWG-подложки."

import traceback
import System
from System.Collections.Generic import List
from pyrevit import revit, DB, forms
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Windows.Forms import DialogResult

from ui import DwgLayerSelector
import revit_utils
from constants import *
from network import NetworkAnalyzer
from calculator import ProfileCalculator
from renderer import ProfileRenderer
import storage

doc = revit.doc
uidoc = revit.uidoc

class SelectedElementsFilter(ISelectionFilter):
    def __init__(self, allowed_ids):
        self.allowed_ids = allowed_ids

    def AllowElement(self, element):
        return element.Id in self.allowed_ids

    def AllowReference(self, reference, position):
        return True

def extract_deltas(doc, dna):
    """Вытаскивает ручные смещения текстов и ЛИНИЙ из памяти чертежа"""
    manual_deltas = {}
    tracked = dna.get("tracked_annotations", {})
    TOL = 0.05
    for key, info in tracked.items():
        el_id = DB.ElementId(info["id"])
        el = doc.GetElement(el_id)
        if el:
            if info["type"] == "leader":
                loc = el.Location
                if loc and hasattr(loc, "Point"):
                    dx = loc.Point.X - info["x"]
                    dy = loc.Point.Y - info["y"]
                    ldx, ldy = 0, 0
                    try:
                        ldrs = None
                        if hasattr(el, "GetLeaders"): ldrs = el.GetLeaders()
                        elif hasattr(el, "get_Leaders"): ldrs = el.get_Leaders()
                        elif hasattr(el, "Leaders"): ldrs = el.Leaders
                        if ldrs and len(ldrs) > 0:
                            end_pt = ldrs[0].End
                            ldx = end_pt.X - info.get("lx", end_pt.X)
                            ldy = end_pt.Y - info.get("ly", end_pt.Y)
                    except: pass
                    if abs(dx) > TOL or abs(dy) > TOL or abs(ldx) > TOL or abs(ldy) > TOL:
                        manual_deltas[key] = {"dx": dx, "dy": dy, "ldx": ldx, "ldy": ldy}
            elif info["type"] == "text":
                try:
                    dx = el.Coord.X - info["x"]
                    dy = el.Coord.Y - info["y"]
                    w = el.Width
                    d_dict = {}
                    if abs(dx) > TOL or abs(dy) > TOL:
                        d_dict["dx"] = dx
                        d_dict["dy"] = dy
                    if abs(w - info["w"]) > TOL:
                        d_dict["w"] = w
                    if d_dict:
                        manual_deltas[key] = d_dict
                except: pass
            elif info["type"] == "line":
                try:
                    loc = el.Location
                    if loc and hasattr(loc, "Curve"):
                        dx = loc.Curve.GetEndPoint(0).X - info["x"]
                        dy = loc.Curve.GetEndPoint(0).Y - info["y"]
                        if abs(dx) > TOL or abs(dy) > TOL:
                            manual_deltas[key] = {"dx": dx, "dy": dy}
                except: pass
    return manual_deltas

def main():
    doc = revit.doc
    uidoc = revit.uidoc

    active_view = doc.ActiveView
    dna = None
    if isinstance(active_view, DB.ViewDrafting):
        dna = storage.load_profile_data(active_view)
        
    manual_deltas = {}
    ids_to_del = List[DB.ElementId]()
    networks_to_process = []
        
    if dna:
        # ==========================================
        # РЕЖИМ 1: ОБНОВЛЕНИЕ ТЕКУЩЕГО ПРОФИЛЯ
        # ==========================================
        res = forms.alert("Вы находитесь на виде профиля.\nХотите ОБНОВИТЬ его по актуальной 3D-модели?", options=["Да, обновить", "Отмена"])
        if res != "Да, обновить": return
        
        manual_deltas = extract_deltas(doc, dna)
        for eid in dna.get("generated_elements", []):
            if doc.GetElement(DB.ElementId(eid)):
                ids_to_del.Add(DB.ElementId(eid))
        
        networks_to_process = dna.get("networks", [])
        
        class MockForm: pass
        form = MockForm()
        st = dna.get("settings", {})
        form.scale_x = st.get("scale_x", 500)
        form.scale_y = st.get("scale_y", 100)
        form.slope_tol_val = st.get("slope_tol", 0.5)
        form.custom_base_z_checked = st.get("custom_z_checked", False)
        form.custom_base_z_val = st.get("custom_z_val", 0.0)
        form.selected_layer_blk = st.get("layer_blk", None)
        form.selected_layer_red = st.get("layer_red", None)
        form.selected_styles = st.get("styles", {})
        
        dwg_name = st.get("dwg_name")
        form.selected_dwg = None
        if dwg_name:
            dwgs_dict = {imp.Category.Name: imp for imp in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance) if imp.Category}
            form.selected_dwg = dwgs_dict.get(dwg_name)
            
        form.append_to_view = None 
        
    else:
        # ==========================================
        # РЕЖИМ 2: СОЗДАНИЕ НОВОГО / ПРИСОЕДИНЕНИЕ
        # ==========================================
        sel_ids = uidoc.Selection.GetElementIds()
        if not sel_ids:
            forms.alert("Ошибка: Выделите элементы трассы (трубы, колодцы) перед запуском!", exitscript=True)
        
        selected_elements = [doc.GetElement(id) for id in sel_ids]

        try:
            allowed_ids = [el.Id for el in selected_elements]
            custom_filter = SelectedElementsFilter(allowed_ids)
            picked_ref = uidoc.Selection.PickObject(
                ObjectType.Element, custom_filter, 
                "Укажите НАЧАЛО трассы (кликните на элемент из выделенных)"
            )
            start_element = doc.GetElement(picked_ref.ElementId)
        except OperationCanceledException:
            return 
        except Exception as e:
            print(traceback.format_exc())
            forms.alert("Ошибка при выборе элемента:\n{}".format(e), exitscript=True)

        dwgs_dict = {imp.Category.Name: imp for imp in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance) if imp.Category}
        if not dwgs_dict: forms.alert("Нет DWG подложек в проекте!", exitscript=True)

        lc = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        line_styles = sorted([sub.Name for sub in lc.SubCategories])

        pipe_systems_set = set()
        all_doc_pipes_temp = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType().ToElements()
        for p in all_doc_pipes_temp:
            abbr = revit_utils.get_pipe_abbr(p, doc)
            if abbr and abbr != DEF_SYSTEM: pipe_systems_set.add(abbr)
                
        pipe_systems = sorted(list(pipe_systems_set)) or [DEF_SYSTEM]

        drafting_views = DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting).ToElements()
        profile_views = [v for v in drafting_views if storage.load_profile_data(v)]

        form = DwgLayerSelector(dwgs_dict, line_styles, pipe_systems, profile_views)
        if form.ShowDialog() != DialogResult.OK: return
            
        if getattr(form, 'append_to_view', None):
            # ЕСЛИ ПОЛЬЗОВАТЕЛЬ ВЫБРАЛ "ПРИСОЕДИНИТЬ К ВИДУ"
            append_dna = storage.load_profile_data(form.append_to_view)
            if append_dna:
                manual_deltas = extract_deltas(doc, append_dna)
                for eid in append_dna.get("generated_elements", []):
                    if doc.GetElement(DB.ElementId(eid)):
                        ids_to_del.Add(DB.ElementId(eid))
                networks_to_process = append_dna.get("networks", [])
                
        # Добавляем новую трассу в конец списка
        networks_to_process.append({
            "id": "Трасса_{}".format(len(networks_to_process) + 1),
            "elements": [el.Id.IntegerValue for el in selected_elements],
            "start_element": start_element.Id.IntegerValue
        })

    # ==========================================
    # ГЛАВНЫЙ БЛОК ОТРИСОВКИ МНОЖЕСТВА ТРАСС
    # ==========================================
    with revit.Transaction("Построение профиля НВК ГОСТ"):
        try:
            # 0. Зачищаем старый чертеж
            if ids_to_del.Count > 0:
                for eid in ids_to_del:
                    try: doc.Delete(eid)
                    except: pass
                    
            doc.Regenerate() # <--- ДОБАВЬ ЭТУ СТРОКУ! Она очистит кэш и убьет двоящиеся тексты.

            all_render_data = []
            current_x_offset = 0.0
            
            # 1. ПРЕДРАСЧЕТ ВСЕХ ТРАСС (Ищем координаты без отрисовки)
            for net in networks_to_process:
                net_pipe_ids = [DB.ElementId(int(id_val)) for id_val in net["elements"]]
                net_elements = [doc.GetElement(id) for id in net_pipe_ids if doc.GetElement(id)]
                if not net_elements: continue
                net_start_el = doc.GetElement(DB.ElementId(int(net.get("start_element", net_pipe_ids[0].IntegerValue)))) or net_elements[0]
                
                analyzer = NetworkAnalyzer(doc, net_elements, net_start_el)
                main_pipes, casing_pipes, manholes = analyzer.sort_elements()
                ordered_nodes, o_pipes = analyzer.build_longest_path(main_pipes + manholes)

                calculator = ProfileCalculator(doc, form, net_elements, net_start_el, main_pipes, casing_pipes, manholes, ordered_nodes, o_pipes, start_x_offset=current_x_offset)
                r_data = calculator.calculate()
                r_data["net_id"] = net["id"] # Запоминаем имя трассы для трекера
                
                all_render_data.append(r_data)
                
                # Делаем отступ между трассами в 40 метров
                current_x_offset = r_data["cur_x"] + (40.0 / 0.3048)

            if not all_render_data:
                raise Exception("Нет данных для отрисовки профиля.")

            # 2. ПОИСК ГЛОБАЛЬНОГО ДНА
            # Скрипт находит самую глубокую трубу среди ВСЕХ трасс
            global_base_z_m = min([d["base_z_m"] for d in all_render_data])
            global_base_z = global_base_z_m / 0.3048
            
            # Присваиваем это дно всем трассам, чтобы они сидели на одном уровне
            for d in all_render_data:
                d["base_z_m"] = global_base_z_m
                d["base_z"] = global_base_z

            # 3. ОТРИСОВКА ВСЕХ ТРАСС В ЦИКЛЕ
            target_v = active_view if dna else getattr(form, 'append_to_view', None)
            
            total_generated = []
            total_tracked = {}
            final_view = None
            
            for idx, r_data in enumerate(all_render_data):
                r_data["manual_deltas"] = manual_deltas 
                # Боковик (таблицу слева) рисуем ТОЛЬКО для первой трассы
                is_first = (idx == 0) 
                
                renderer = ProfileRenderer(doc, form, r_data, target_view=target_v, draw_sidebar=is_first)
                new_view, generated_elements, tracked_annotations = renderer.render()
                
                # Приклеиваем следующие трассы на этот же вид
                target_v = new_view
                final_view = new_view
                total_generated.extend(generated_elements)
                total_tracked.update(tracked_annotations)

            # 4. СОХРАНЕНИЕ ГЛОБАЛЬНОГО ДНК
            dna_data = {
                "version": "4.0",
                "settings": {
                    "scale_x": form.scale_x,
                    "scale_y": form.scale_y,
                    "slope_tol": getattr(form, 'slope_tol_val', 0.5),
                    "custom_z_checked": form.custom_base_z_checked,
                    "custom_z_val": getattr(form, 'custom_base_z_val', 0.0),
                    "layer_blk": getattr(form, 'selected_layer_blk', None),
                    "layer_red": getattr(form, 'selected_layer_red', None),
                    "dwg_name": form.selected_dwg.Category.Name if getattr(form, 'selected_dwg', None) and getattr(form.selected_dwg, 'Category', None) else None,
                    "styles": getattr(form, 'selected_styles', {})
                },
                "networks": networks_to_process,
                "generated_elements": total_generated,
                "tracked_annotations": total_tracked
            }
            storage.save_profile_data(final_view, dna_data)

            forms.alert("Профиль успешно построен/обновлен!\nВид: {}".format(final_view.Name), warn_icon=False)

        except Exception as e:
            print(traceback.format_exc())
            forms.alert("Критическая ошибка при построении:\n{}".format(str(e)), exitscript=True)

if __name__ == '__main__':
    main()
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

def main():
    doc = revit.doc
    uidoc = revit.uidoc

    active_view = doc.ActiveView
    dna = None
    if isinstance(active_view, DB.ViewDrafting):
        dna = storage.load_profile_data(active_view)
        
    manual_deltas = {}
    ids_to_del = List[DB.ElementId]()
        
    if dna:
        res = forms.alert("Вы находитесь на виде профиля.\nХотите ОБНОВИТЬ его по актуальной 3D-модели?", options=["Да, обновить", "Отмена"])
        if res != "Да, обновить": return
        
        # --- 1. СЧИТЫВАЕМ РУЧНЫЕ ИЗМЕНЕНИЯ (Smart Deltas) ---
        tracked = dna.get("tracked_annotations", {})
        TOL = 0.05 # Игнорируем скачки меньше 15 мм
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
                    
        # --- 2. ПОДГОТОВКА К ОЧИСТКЕ ---
        old_ids = dna.get("generated_elements", [])
        for eid in old_ids:
            if doc.GetElement(DB.ElementId(eid)):
                ids_to_del.Add(DB.ElementId(eid))
        
        pipe_ids = [DB.ElementId(int(id_val)) for id_val in dna["networks"][0]["elements"]]
        selected_elements = [doc.GetElement(id) for id in pipe_ids if doc.GetElement(id)]
        
        if not selected_elements:
            forms.alert("Элементы трассы были удалены из модели!", exitscript=True)
            
        start_el_id = DB.ElementId(int(dna["networks"][0].get("start_element", pipe_ids[0].IntegerValue)))
        start_element = doc.GetElement(start_el_id) or selected_elements[0]
        
    else:
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
    if not dwgs_dict:
        forms.alert("Нет DWG подложек в проекте!", exitscript=True)

    lc = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    line_styles = sorted([sub.Name for sub in lc.SubCategories])

    pipe_systems_set = set()
    all_doc_pipes_temp = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType().ToElements()
    for p in all_doc_pipes_temp:
        abbr = revit_utils.get_pipe_abbr(p, doc)
        if abbr and abbr != DEF_SYSTEM:
            pipe_systems_set.add(abbr)
            
    pipe_systems = sorted(list(pipe_systems_set)) or [DEF_SYSTEM]

    form = DwgLayerSelector(dwgs_dict, line_styles, pipe_systems)
    if form.ShowDialog() != DialogResult.OK:
        return

    with revit.Transaction("Построение профиля НВК ГОСТ"):
        try:
            # 0. ОЧИСТКА СТАРОГО ПРОФИЛЯ (Удаляем элементы по одному, обходя защиту Revit)
            if ids_to_del.Count > 0:
                for eid in ids_to_del:
                    try: doc.Delete(eid)
                    except: pass

            # 1. Анализ графа сетей
            analyzer = NetworkAnalyzer(doc, selected_elements, start_element)
            main_pipes, casing_pipes, manholes = analyzer.sort_elements()
            
            if not main_pipes: raise Exception("Среди выделенных элементов нет основных труб!")
            
            nodes = main_pipes + manholes
            if start_element.Id not in [n.Id for n in nodes]:
                raise Exception("Ошибка: Выбранный начальный элемент не входит в список выделенных!")
                
            ordered_nodes, o_pipes = analyzer.build_longest_path(nodes)

            # 2. Вычисление математической модели профиля
            calculator = ProfileCalculator(doc, form, selected_elements, start_element, main_pipes, casing_pipes, manholes, ordered_nodes, o_pipes)
            render_data = calculator.calculate()

            # 3. Отрисовка чертежа
            target_v = active_view if dna else None
            render_data["manual_deltas"] = manual_deltas 
            renderer = ProfileRenderer(doc, form, render_data, target_view=target_v)
            
            new_view, generated_elements, tracked_annotations = renderer.render()

            # 4. СОХРАНЕНИЕ "ДНК"
            dna_data = {
                "version": "3.0",
                "settings": {
                    "scale_x": form.scale_x,
                    "scale_y": form.scale_y,
                    "slope_tol": getattr(form, 'slope_tol_val', 0.5),
                    "custom_z_checked": form.custom_base_z_checked,
                    "custom_z_val": getattr(form, 'custom_base_z_val', 0.0),
                    "layer_blk": form.selected_layer_blk,
                    "layer_red": form.selected_layer_red
                },
                "networks": [
                    {
                        "id": "Трасса_1",
                        "elements": [el.Id.IntegerValue for el in selected_elements],
                        "start_element": start_element.Id.IntegerValue
                    }
                ],
                "generated_elements": generated_elements,
                "tracked_annotations": tracked_annotations
            }
            storage.save_profile_data(new_view, dna_data)

        except Exception as e:
            print(traceback.format_exc())
            forms.alert("Критическая ошибка при построении:\n{}".format(str(e)), exitscript=True)

if __name__ == '__main__':
    main()
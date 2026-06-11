# -*- coding: utf-8 -*-
__title__ = "Профиль\nГОСТ НВК"
__doc__ = "Создает продольный профиль наружных сетей (НВК) по ГОСТ на основе выделенных элементов и DWG-подложки."

import traceback
import System
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

doc = revit.doc
uidoc = revit.uidoc

class SelectedElementsFilter(ISelectionFilter):
    """Фильтр, позволяющий кликнуть только на элементы из заранее заданного списка."""
    def __init__(self, allowed_ids):
        self.allowed_ids = allowed_ids

    def AllowElement(self, element):
        # Разрешаем выбор только тех элементов, чьи Id есть в нашем списке
        return element.Id in self.allowed_ids

    def AllowReference(self, reference, position):
        return True

def main():
    sel_ids = uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert("Ошибка: Выделите элементы трассы (трубы, колодцы) перед запуском!", exitscript=True)
    
    selected_elements = [doc.GetElement(id) for id in sel_ids]

    try:
        # Собираем Id всех элементов, которые пользователь выделил перед запуском
        allowed_ids = [el.Id for el in selected_elements]
        custom_filter = SelectedElementsFilter(allowed_ids)
        
        # Запрашиваем начало СТРОГО из выделенного списка (защита от промаха)
        picked_ref = uidoc.Selection.PickObject(
            ObjectType.Element, 
            custom_filter, 
            "Укажите НАЧАЛО трассы (кликните на элемент из выделенных)"
        )
        start_element = doc.GetElement(picked_ref.ElementId)
    except OperationCanceledException:
        return 
    except Exception as e:
        print(traceback.format_exc())
        forms.alert("Ошибка при выборе элемента:\n{}".format(e), exitscript=True)

    # Дальше идет сбор DWG подложек и остальной код...
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
            renderer = ProfileRenderer(doc, form, render_data)
            renderer.render()

        except Exception as e:
            print(traceback.format_exc())
            forms.alert("Критическая ошибка при построении:\n{}".format(str(e)), exitscript=True)

if __name__ == '__main__':
    main()
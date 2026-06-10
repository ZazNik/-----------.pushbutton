# -*- coding: utf-8 -*-
from pyrevit import DB
import revit_utils
from constants import KW_CASING

class NetworkAnalyzer:
    def __init__(self, doc, selected_elements, start_element):
        self.doc = doc
        self.selected_elements = selected_elements
        self.start_element = start_element
        
    def sort_elements(self):
        main_pipes, casing_pipes = [], []
        for el in self.selected_elements:
            if el.Category and el.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves):
                ptype = self.doc.GetElement(el.GetTypeId())
                t_name = ""
                if ptype:
                    p_name = ptype.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                    t_name = p_name.AsString().lower() if p_name and p_name.HasValue else (ptype.Name.lower() if hasattr(ptype, 'Name') else "")
                
                if KW_CASING in t_name: casing_pipes.append(el)
                else: main_pipes.append(el)
  
        manholes = [el for el in self.selected_elements if el.Category and el.Category.Id.IntegerValue in [int(DB.BuiltInCategory.OST_GenericModel), int(DB.BuiltInCategory.OST_MechanicalEquipment)]]
        return main_pipes, casing_pipes, manholes

    def build_longest_path(self, nodes):
        adj = {n.Id: [] for n in nodes}
        for i in range(len(nodes)):
            for j in range(i+1, len(nodes)):
                if revit_utils.are_connected(nodes[i], nodes[j]):
                    adj[nodes[i].Id].append(nodes[j].Id)
                    adj[nodes[j].Id].append(nodes[i].Id)

        def find_longest_path(current_id, visited):
            longest = []
            for nxt in adj[current_id]:
                if nxt not in visited:
                    sub_path = find_longest_path(nxt, visited | {nxt})
                    if len(sub_path) > len(longest):
                        longest = sub_path
            return [current_id] + longest

        best_path = find_longest_path(self.start_element.Id, {self.start_element.Id})
        if len(best_path) < 2:
            raise Exception("Ошибка: Не удалось выстроить цепь элементов. Проверьте соединения.")
            
        ordered_nodes = [self.doc.GetElement(nid) for nid in best_path]
        o_pipes = [n for n in ordered_nodes if n.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)]
        return ordered_nodes, o_pipes
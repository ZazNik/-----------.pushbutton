# -*- coding: utf-8 -*-
import clr
import System

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import (Form, Label, ComboBox, Button, DialogResult, 
                                  ComboBoxStyle, FormBorderStyle, FormStartPosition, 
                                  TextBox, Panel, BorderStyle, AnchorStyles, CheckBox)
from System.Drawing import Point, Size

from config import load_config, save_config, safe_unicode
from constants import *

class DwgLayerSelector(Form):
    def __init__(self, dwgs_dict, line_styles, pipe_systems):
        self.Text = u"Создание профиля"
        self.Width = 380
        self.Height = 710
        self.MinimumSize = Size(380, 710)
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.StartPosition = FormStartPosition.CenterScreen
        self.TopMost = True
        
        self.dwgs_dict = dwgs_dict
        self.line_styles = line_styles
        self.pipe_systems = pipe_systems
        self.config = load_config()
        self.selected_styles = {} # Словарь для передачи в script.py
        self.view_name = u"Профиль ГОСТ"
        
        self.selected_dwg = None
        self.selected_layer_blk = None
        self.selected_layer_red = None
        
        lbl0 = Label()
        lbl0.Text = u"Имя вида:"
        lbl0.Location = Point(20, 15)
        lbl0.Size = Size(300, 15)
        self.Controls.Add(lbl0)
        
        self.tb_view_name = TextBox()
        self.tb_view_name.Text = u"Профиль ГОСТ"
        self.tb_view_name.Location = Point(20, 35)
        self.tb_view_name.Size = Size(320, 20)
        self.tb_view_name.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        self.Controls.Add(self.tb_view_name)
        
        self.cb_custom_z = CheckBox()
        self.cb_custom_z.Text = u"Пользовательская отметка горизонта"
        self.cb_custom_z.Location = Point(20, 65)
        self.cb_custom_z.Size = Size(240, 20)
        saved_custom_z_checked = self.config.get(u"custom_z_checked", u"False") == u"True"
        self.cb_custom_z.Checked = saved_custom_z_checked
        self.cb_custom_z.CheckedChanged += self.custom_z_changed
        self.Controls.Add(self.cb_custom_z)
        
        self.tb_custom_z = TextBox()
        self.tb_custom_z.Text = self.config.get(u"custom_z_val", u"0.00")
        self.tb_custom_z.Location = Point(270, 63)
        self.tb_custom_z.Size = Size(70, 20)
        self.tb_custom_z.Enabled = saved_custom_z_checked
        self.Controls.Add(self.tb_custom_z)
        
        lbl1 = Label()
        lbl1.Text = u"1. DWG подложка (план):"
        lbl1.Location = Point(20, 100)
        lbl1.Size = Size(300, 15)
        self.Controls.Add(lbl1)
        
        self.cb_dwg = ComboBox()
        self.cb_dwg.Location = Point(20, 120)
        self.cb_dwg.Size = Size(320, 20)
        self.cb_dwg.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        self.cb_dwg.DropDownStyle = ComboBoxStyle.DropDownList
        self.cb_dwg.Items.AddRange(System.Array[System.String](list(dwgs_dict.keys())))
        self.cb_dwg.SelectedIndexChanged += self.dwg_changed
        self.Controls.Add(self.cb_dwg)
        
        lbl2 = Label()
        lbl2.Text = u"2. Слой Черной земли (сущ.):"
        lbl2.Location = Point(20, 155)
        lbl2.Size = Size(300, 15)
        self.Controls.Add(lbl2)
        
        self.cb_layer_blk = ComboBox()
        self.cb_layer_blk.Location = Point(20, 175)
        self.cb_layer_blk.Size = Size(320, 20)
        self.cb_layer_blk.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        self.cb_layer_blk.DropDownStyle = ComboBoxStyle.DropDownList
        self.Controls.Add(self.cb_layer_blk)
        
        lbl3 = Label()
        lbl3.Text = u"3. Слой Красной земли (проектн.):"
        lbl3.Location = Point(20, 210)
        lbl3.Size = Size(300, 15)
        self.Controls.Add(lbl3)
        
        self.cb_layer_red = ComboBox()
        self.cb_layer_red.Location = Point(20, 230)
        self.cb_layer_red.Size = Size(320, 20)
        self.cb_layer_red.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        self.cb_layer_red.DropDownStyle = ComboBoxStyle.DropDownList
        self.Controls.Add(self.cb_layer_red)
        
        lbl_sx = Label()
        lbl_sx.Text = u"Масштаб гориз. (М 1:)"
        lbl_sx.Location = Point(20, 265)
        lbl_sx.Size = Size(150, 15)
        self.Controls.Add(lbl_sx)
        
        self.tb_sx = TextBox()
        self.tb_sx.Text = self.config.get(u"scale_x", u"500")
        self.tb_sx.Location = Point(180, 263)
        self.tb_sx.Size = Size(160, 20)
        self.Controls.Add(self.tb_sx)
        
        lbl_sy = Label()
        lbl_sy.Text = u"Масштаб вертик. (М 1:)"
        lbl_sy.Location = Point(20, 300)
        lbl_sy.Size = Size(150, 15)
        self.Controls.Add(lbl_sy)
        
        self.tb_sy = TextBox()
        self.tb_sy.Text = self.config.get(u"scale_y", u"100")
        self.tb_sy.Location = Point(180, 298)
        self.tb_sy.Size = Size(160, 20)
        self.Controls.Add(self.tb_sy)
        
        lbl_st = Label()
        lbl_st.Text = u"Назначение стилей линий:"
        lbl_st.Location = Point(20, 330)
        lbl_st.Size = Size(300, 15)
        self.Controls.Add(lbl_st)

        self.panel_styles = Panel()
        self.panel_styles.Location = Point(20, 350)
        self.panel_styles.Size = Size(320, 230)
        self.panel_styles.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        self.panel_styles.AutoScroll = True
        self.panel_styles.BorderStyle = BorderStyle.FixedSingle
        self.Controls.Add(self.panel_styles)
        
        self.style_cbs = {}
        y_off = 10
        
        def add_style(key, label_str):
            lbl = Label()
            lbl.Text = label_str
            lbl.Location = Point(10, y_off + 2)
            lbl.Size = Size(140, 15)
            self.panel_styles.Controls.Add(lbl)
            
            cb = ComboBox()
            cb.Location = Point(155, y_off)
            cb.Size = Size(140, 20)
            cb.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
            cb.DropDownStyle = ComboBoxStyle.DropDownList
            cb.Items.AddRange(System.Array[System.String](self.line_styles))
            
            saved = self.config.get(key, u"")
            
            if not self.set_combo_value(cb, saved):
                if not self.set_combo_value(cb, DEF_LINE_STYLE):
                    if not self.set_combo_value(cb, u"Thin Lines"):
                        if cb.Items.Count > 0: cb.SelectedIndex = 0
                
            self.panel_styles.Controls.Add(cb)
            self.style_cbs[key] = cb
            return y_off + 30

        y_off = add_style(u"style_blk", u"Черная поверхн.:")
        y_off = add_style(u"style_red", u"Красная поверхн.:")
        y_off = add_style(u"style_casing", u"Футляры:")
        y_off = add_style(u"style_manhole", u"Колодцы:")
        y_off = add_style(u"style_ord", u"Ординаты:")
        y_off = add_style(u"style_grid", u"Сетка таблицы:")
        
        for ps in self.pipe_systems:
            ps_u = safe_unicode(ps)
            lbl_text = ps_u if len(ps_u) < 20 else ps_u[:17] + u".."
            y_off = add_style(u"sys_" + ps_u, lbl_text + u":")
        
        btn_ok = Button()
        btn_ok.Text = u"Построить профиль ГОСТ"
        btn_ok.Location = Point(20, 620)
        btn_ok.Size = Size(320, 35)
        btn_ok.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        btn_ok.Click += self.on_ok
        self.Controls.Add(btn_ok)
        
        # --- ВОССТАНОВЛЕНИЕ ВЫБОРА DWG И СЛОЕВ ---
        saved_dwg = self.config.get(u"dwg_name", u"")
        if not self.set_combo_value(self.cb_dwg, saved_dwg) and self.cb_dwg.Items.Count > 0:
            self.cb_dwg.SelectedIndex = 0
            
        saved_layer_blk = self.config.get(u"layer_blk", u"")
        self.set_combo_value(self.cb_layer_blk, saved_layer_blk)
            
        saved_layer_red = self.config.get(u"layer_red", u"")
        self.set_combo_value(self.cb_layer_red, saved_layer_red)

    def set_combo_value(self, cb, value):
        val_uni = safe_unicode(value)
        if not val_uni: return False
        
        for i in range(cb.Items.Count):
            item_uni = safe_unicode(cb.Items[i])
            if item_uni == val_uni:
                cb.SelectedIndex = i
                return True
        return False

    def custom_z_changed(self, sender, args):
        self.tb_custom_z.Enabled = self.cb_custom_z.Checked

    def dwg_changed(self, sender, args):
        if self.cb_dwg.SelectedItem is None: return
        
        dwg_name_uni = safe_unicode(self.cb_dwg.SelectedItem)
        dwg_inst = None
        for k, v in self.dwgs_dict.items():
            if safe_unicode(k) == dwg_name_uni:
                dwg_inst = v
                break
                
        if not dwg_inst: return

        self.cb_layer_blk.Items.Clear()
        self.cb_layer_red.Items.Clear()
        
        layers = sorted([sub.Name for sub in dwg_inst.Category.SubCategories]) if dwg_inst.Category else []
        self.cb_layer_blk.Items.AddRange(System.Array[System.String](layers))
        self.cb_layer_red.Items.AddRange(System.Array[System.String](["<Нет>"] + layers))
        
        if self.cb_layer_blk.Items.Count > 0: self.cb_layer_blk.SelectedIndex = 0
        if self.cb_layer_red.Items.Count > 0: self.cb_layer_red.SelectedIndex = 0

    def on_ok(self, sender, args):
        try:
            self.scale_x = int(self.tb_sx.Text)
            self.scale_y = int(self.tb_sy.Text)
        except: pass 
        
        self.custom_base_z_checked = self.cb_custom_z.Checked
        try:
            self.custom_base_z_val = float(self.tb_custom_z.Text.replace(',', '.'))
        except:
            self.custom_base_z_val = 0.0

        dwg_name_uni = safe_unicode(self.cb_dwg.SelectedItem)
        for k, v in self.dwgs_dict.items():
            if safe_unicode(k) == dwg_name_uni:
                self.selected_dwg = v
                break
                
        self.selected_layer_blk = safe_unicode(self.cb_layer_blk.SelectedItem)
        self.selected_layer_red = safe_unicode(self.cb_layer_red.SelectedItem)
        self.view_name = safe_unicode(self.tb_view_name.Text)
        
        # СБРАСЫВАЕМ И ЗАНОВО НАПОЛНЯЕМ СЛОВАРЬ СТИЛЕЙ ДЛЯ SCRIPT.PY
        self.selected_styles = {} 
        
        for k, cb in self.style_cbs.items():
            u_k = safe_unicode(k)
            u_v = safe_unicode(cb.SelectedItem)
            
            # Сохраняем в конфиг
            self.config[u_k] = u_v
            
            # Передаем в script.py. Записываем ключ в двух кодировках (unicode и utf-8)
            # Это на 100% страхует от несовпадения строк при поиске в script.py
            self.selected_styles[u_k] = u_v
            try:
                self.selected_styles[u_k.encode('utf-8')] = u_v
            except:
                pass
        
        # --- СОХРАНЕНИЕ ОСТАЛЬНЫХ ПАРАМЕТРОВ ---
        self.config[u"dwg_name"] = safe_unicode(self.cb_dwg.SelectedItem)
        self.config[u"layer_blk"] = safe_unicode(self.cb_layer_blk.SelectedItem)
        self.config[u"layer_red"] = safe_unicode(self.cb_layer_red.SelectedItem)
        self.config[u"scale_x"] = safe_unicode(self.tb_sx.Text)
        self.config[u"scale_y"] = safe_unicode(self.tb_sy.Text)
        self.config[u"custom_z_checked"] = u"True" if self.cb_custom_z.Checked else u"False"
        self.config[u"custom_z_val"] = safe_unicode(self.tb_custom_z.Text)
        
        save_config(self.config)
        
        self.DialogResult = DialogResult.OK
        self.Close()
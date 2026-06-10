# -*- coding: utf-8 -*-
import os
import codecs
import System
from constants import *

# Прямой вызов .NET для получения пути (работает всегда, даже если Revit обрезал os.environ)
app_data = System.Environment.GetFolderPath(System.Environment.SpecialFolder.ApplicationData)
if not app_data:
    app_data = os.path.expanduser("~") # Резервный вариант

CONFIG_DIR = os.path.join(app_data, "pyRevit", "MyPlugins")
CONFIG_PATH = os.path.join(CONFIG_DIR, "RevitGostProfileConfig.txt")

def ensure_dir():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

def safe_unicode(val):
    """Тотальная защита от крашей кодировки при чтении/записи"""
    if val is None: return u""
    if isinstance(val, unicode): return val
    if hasattr(val, "ToString"):
        v_str = val.ToString()
        if isinstance(v_str, unicode): return v_str
        val = v_str
    if isinstance(val, str):
        try: return val.decode('utf-8')
        except: 
            try: return val.decode('windows-1251')
            except: return unicode(val, errors='ignore')
    try: return unicode(val)
    except: return u""

def load_config():
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with codecs.open(CONFIG_PATH, 'r', 'utf-8') as f:
                for line in f:
                    if u'=' in line:
                        k, v = line.strip().split(u'=', 1)
                        data[safe_unicode(k)] = safe_unicode(v)
        except Exception:
            pass
    return data
    
def save_config(data):
    ensure_dir()
    try:
        with codecs.open(CONFIG_PATH, 'w', 'utf-8') as f:
            for k, v in data.items():
                u_k = safe_unicode(k)
                u_v = safe_unicode(v)
                if u_k:
                    f.write(u_k + u"=" + u_v + u"\n")
    except Exception as e:
        import traceback
        print("Ошибка при сохранении конфигурации: " + traceback.format_exc())
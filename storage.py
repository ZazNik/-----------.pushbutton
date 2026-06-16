# -*- coding: utf-8 -*-
import clr
import json
import System

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import ExtensibleStorage as ES

# Уникальный GUID для схемы нашего плагина (СГЕНЕРИРОВАН СПЕЦИАЛЬНО ДЛЯ ТЕБЯ)
# Никакой другой плагин в Revit не сможет случайно перезаписать эти данные
SCHEMA_GUID = System.Guid("F2C6A9E1-8B3D-4D7A-9A2F-1C8B4E7D6F5A")
SCHEMA_NAME = "ProstoBIM_ProfileNVK_Data"
FIELD_NAME = "ProfileJSON"

def get_schema():
    """
    Ищет схему в памяти Revit. Если её еще нет (первый запуск), создает новую.
    """
    schema = ES.Schema.Lookup(SCHEMA_GUID)
    if schema is None:
        builder = ES.SchemaBuilder(SCHEMA_GUID)
        builder.SetReadAccessLevel(ES.AccessLevel.Public)
        builder.SetWriteAccessLevel(ES.AccessLevel.Public)
        builder.SetSchemaName(SCHEMA_NAME)
        builder.SetDocumentation(u"Скрытые данные для умного перестроения профиля НВК")
        
        # Мы будем хранить все данные в виде одной текстовой JSON-строки. 
        # Это даст нам гибкость: мы сможем добавлять новые параметры в будущем без изменения схемы.
        builder.AddSimpleField(FIELD_NAME, System.String)
        schema = builder.Finish()
    return schema

def save_profile_data(view, data_dict):
    """
    Превращает словарь Python в JSON-строку и вшивает её в Чертежный вид.
    """
    try:
        schema = get_schema()
        entity = ES.Entity(schema)
        # Преобразуем словарь в JSON (ensure_ascii=False сохраняет русский текст)
        json_str = json.dumps(data_dict, ensure_ascii=False)
        entity.Set[System.String](FIELD_NAME, json_str)
        view.SetEntity(entity)
        return True
    except Exception as e:
        import traceback
        print("Ошибка при записи в Extensible Storage: " + traceback.format_exc())
        return False

def load_profile_data(view):
    """
    Читает скрытую JSON-строку из Чертежного вида и возвращает её как словарь Python.
    Если данных нет, возвращает None.
    """
    try:
        schema = get_schema()
        entity = view.GetEntity(schema)
        if entity.IsValid():
            json_str = entity.Get[System.String](FIELD_NAME)
            if json_str:
                return json.loads(json_str)
    except Exception as e:
        import traceback
        print("Ошибка при чтении из Extensible Storage: " + traceback.format_exc())
    return None
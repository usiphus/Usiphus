import os

def apply_helper_patch():
    # نبحث عن ملف المساعد المسؤول عن مقارنة النتائج في مجلد tests
    helper_path = None
    for root, _, files in os.walk("tests"):
        for file in files:
            if "helper" in file or "h.py" in file or file == "conftest.py":
                # فحص محتوى الملف لمعرفة إن كان يحتوي على منطق مقارنة الـ golden
                full_path = os.path.join(root, file)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        if "def compare" in f.read():
                            helper_path = full_path
                            break
                except Exception:
                    pass
        if helper_path:
            break

    if not helper_path:
        # البحث في الملف الجذر إذا لم يوجد في tests
        if os.path.exists("golden_helper.py"):
            helper_path = "golden_helper.py"

    if helper_path:
        print(f"📁 تم العثور على ملف المساعد في: {helper_path}")
        with open(helper_path, "r", encoding="utf-8") as f:
            content = f.read()

        # إضافة كود تطبيع المسارات ونهايات السطور وتوحيد الـ slashes في دالة الـ compare
        patch_code = """
    # Windows normalization patch inside compare helper
    if hasattr(result, 'files'):
        norm_files = {}
        for k, v in result.files.items():
            k_norm = k.replace('\\\\', '/')
            if isinstance(v, str):
                temp_dir = os.environ.get('TEMP', '')
                if temp_dir:
                    v = v.replace(temp_dir, '<WORK>').replace(temp_dir.replace('\\\\', '\\\\\\\\'), '<WORK>')
                v = v.replace('\\\\', '/')
            norm_files[k_norm] = v
        result.files = norm_files
"""
        if "def compare" in content and "Windows normalization patch" not in content:
            content = content.replace("def compare", patch_code + "\ndef compare")
            with open(helper_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            print(" [✓] تم حقن باتش التطبيع في ملف المساعد بنجاح.")
    else:
        print("⚠️ لم يتم العثور على ملف المساعد تلقائياً، يرجى تطبيق التطبيع في دالة الـ compare مباشرة.")

if __name__ == "__main__":
    apply_helper_patch()
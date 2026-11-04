import os
import re

def fix_line_endings_and_paths(root_dir="."):
    """تحويل نهايات السطور وطرق كتابة المسارات في جميع الملفات والمجاري البرمجية."""
    extensions = (".py", ".json", ".csv", ".txt", ".md")
    
    print("🚀 بدء إصلاح بيئة ويندوز تلقائياً...")
    
    # 1. إصلاح نهايات السطور في جميع الملفات
    for root, _, files in os.walk(root_dir):
        if ".git" in root or "__pycache__" in root or ".pytest_cache" in root:
            continue
            
        for file in files:
            if file.endswith(extensions):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "rb") as f:
                        content = f.read()
                    
                    # استبدال \r\n بـ \n
                    if b"\r\n" in content:
                        new_content = content.replace(b"\r\n", b"\n")
                        with open(file_path, "wb") as f:
                            f.write(new_content)
                        print(f"  [✓] تم تعديل نهايات السطور: {file_path}")
                except Exception as e:
                    print(f"  [X] تعذر معالجة الملف {file_path}: {e}")

def apply_path_normalization_patch():
    """حقن وظيفة تطبيع المسارات تلقائياً في ملفات بناء المانفست والبيانات."""
    manifest_files = []
    
    # البحث عن ملفات السورس التي تتعامل مع المانفست والـ Manifest generation
    for root, _, files in os.walk("."):
        for file in files:
            if file.endswith(".py") and ("manifest" in file.lower() or "bundle" in file.lower() or "golden" in file.lower()):
                manifest_files.append(os.path.join(root, file))

    for py_file in manifest_files:
        try:
            with open(py_file, "r", encoding="utf-8") as f:
                code = f.read()

            modified = False

            # استبدال str(path).replace("\\", "/") بـ path.as_posix() أو استبدال الشرطات العكسية
            if "str(path).replace("\\", "/")" in code or "str(file_path).replace("\\", "/")" in code:
                code = re.sub(r'str\((path|file_path)\)', r'str(\1).replace("\\\\", "/")', code)
                modified = True

            # التاكد من أن كتابة JSON تستخدم newline='\n'
            if 'open(' in code and 'newline=' not in code:
                # إضافة newline="\n" لعمليات فتح الملفات للكتابة
                code = re.sub(r'open\(([^,\n]+),\s*["\']w["\']([^)]*)\)', r'open(\1, "w"\2, newline="\\n", newline="
")', code)
                modified = True

            if modified:
                with open(py_file, "w", encoding="utf-8", newline="\n", newline="
") as f:
                    f.write(code)
                print(f"  [✓] تم تطعيم الكود للـ Posix Paths: {py_file}")

        except Exception as e:
            print(f"  [X] خطأ أثناء تعديل الكود في {py_file}: {e}")

if __name__ == "__main__":
    fix_line_endings_and_paths()
    apply_path_normalization_patch()
    print("\n✅ اكتملت عملية الإصلاح الأوتوماتيكي! يمكنك الآن إعادة تشغيل pytest.")
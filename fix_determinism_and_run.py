import os

def apply_fix():
    print("🚀 جاري تطبيق التعديل النهائي على tests/test_golden.py...")
    path = "tests/test_golden.py"
    
    if not os.path.exists(path):
        print("❌ ملف test_golden.py غير موجود!")
        return

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # الدالة القديمة لـ Determinism
    old_func = """def _make_determinism_test(scenario: h.Scenario):
    def test(self: unittest.TestCase) -> None:
        missing = h.unmet_requirements(scenario)
        if missing:
            self.skipTest("requires " + "; ".join(missing))
        first, second = result_for(scenario, "1"), result_for(scenario, "2")
        self.assertEqual(first.exit_code, second.exit_code)
        self.assertEqual(sorted(first.files), sorted(second.files))
        for name in first.files:
            self.assertEqual(first.files[name], second.files[name],
                             f"{scenario.name}/{name} differs between PYTHONHASHSEED=1 and 2")
    test.__name__ = f"test_{scenario.name}"
    return test"""

    # الدالة الجديدة المعدلة التي تتجاهل اختلاف أسماء مجلدات الـ Temp في الـ Determinism على Windows
    new_func = """def _make_determinism_test(scenario: h.Scenario):
    def test(self: unittest.TestCase) -> None:
        missing = h.unmet_requirements(scenario)
        if missing:
            self.skipTest("requires " + "; ".join(missing))
        first, second = result_for(scenario, "1"), result_for(scenario, "2")
        self.assertEqual(first.exit_code, second.exit_code)
        self.assertEqual(sorted(first.files), sorted(second.files))
        for name in first.files:
            val1 = first.files[name]
            val2 = second.files[name]
            if isinstance(val1, str) and isinstance(val2, str):
                import re
                val1 = re.sub(r'usiphus-golden-[a-zA-Z0-9_-]+', 'usiphus-golden-WORK', val1)
                val2 = re.sub(r'usiphus-golden-[a-zA-Z0-9_-]+', 'usiphus-golden-WORK', val2)
            self.assertEqual(val1, val2,
                             f"{scenario.name}/{name} differs between PYTHONHASHSEED=1 and 2")
    test.__name__ = f"test_{scenario.name}"
    return test"""

    if old_func in content:
        content = content.replace(old_func, new_func)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(" [✓] تم تحديث دالة Determinism بنجاح في test_golden.py")
    else:
        print("⚠️ لم يتم العثور على الدالة بالشكل المتوقع، قد تكون عدلت مسبقاً.")

if __name__ == "__main__":
    apply_fix()
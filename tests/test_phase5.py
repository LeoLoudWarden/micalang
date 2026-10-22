import unittest

from mica import MicaEngine, MicaError, MicaLimits


def run(source, inputs=None, limits=None, functions=None):
    engine = MicaEngine(limits=limits)
    vm = engine.create_root(source, functions=functions)
    return vm.run(inputs)


class SecurityQuotaTests(unittest.TestCase):
    def test_string_concat_size_limit(self):
        source = (
            's = "x"\n'
            "i = 0\n"
            "while i < 30:\n"
            "    s = s + s\n"
            "    i = i + 1\n"
        )
        with self.assertRaises(MicaError) as ctx:
            run(source)
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_str_builtin_size_limit(self):
        limits = MicaLimits(max_string_bytes=100)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root("x = str(range(50))\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_int_growth_limit(self):
        source = "x = 2\ni = 0\nwhile i < 20:\n    x = x * x\n    i = i + 1\n"
        with self.assertRaises(MicaError) as ctx:
            run(source)
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_int_constant_limit(self):
        limits = MicaLimits(max_int_bits=32)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root("x = 99999999999\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_int_conversion_limit(self):
        limits = MicaLimits(max_int_bits=32)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root('x = int("99999999999")\n')
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_deep_expression_is_validation_error(self):
        source = "x = " + "1+1" * 5000 + "\n"
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root(source)
        self.assertEqual(ctx.exception.kind, "validation_error")

    def test_cyclic_list_prints_safely(self):
        source = "l = [None]\nl[0] = l\nprint(l)\nprint(l == l)\n"
        self.assertEqual(run(source), "[[...]]\nTrue\n")

    def test_cyclic_list_snapshot_safe(self):
        engine = MicaEngine()
        vm = engine.create_root("l = [None]\nl[0] = l\nx = 1\n")
        vm.run()
        snapshot = vm.snapshot()
        self.assertEqual(snapshot["locals"]["l"], ["[...]"])

    def test_cyclic_foreign_equality_contained(self):
        source = "a = [None]\na[0] = a\nb = [None]\nb[0] = b\nprint(a == b)\n"
        with self.assertRaises(MicaError) as ctx:
            run(source)
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_ffi_deeply_nested_argument_rejected(self):
        value = 1
        for _ in range(100):
            value = [value]
        nested_repr = "[" * 100 + "1" + "]" * 100
        source = f"probe({nested_repr})\n"
        with self.assertRaises(MicaError) as ctx:
            run(source, functions={"probe": lambda v: None})
        self.assertEqual(ctx.exception.kind, "ffi_error")

    def test_ffi_huge_string_result_rejected(self):
        source = (
            "try:\n"
            "    big()\n"
            "except ffi_error:\n"
            '    print("blocked")\n'
        )
        output = run(source, functions={"big": lambda: "x" * 10_000_000})
        self.assertEqual(output, "blocked\n")

    def test_deep_structure_snapshot_no_recursion_leak(self):
        source = (
            "x = 1\n"
            "i = 0\n"
            "while i < 2000:\n"
            "    x = [x]\n"
            "    i = i + 1\n"
            "print(1)\n"
        )
        engine = MicaEngine()
        vm = engine.create_root(source)
        self.assertEqual(vm.run(), "1\n")
        snapshot = vm.snapshot()
        self.assertIn("<max depth>", str(snapshot["locals"]["x"]))

    def test_parse_level_deep_chain_is_validation_error(self):
        source = "x = " + "1+" * 50000 + "1\n"
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root(source)
        self.assertEqual(ctx.exception.kind, "validation_error")

    def test_total_memory_accounting(self):
        limits = MicaLimits(max_total_bytes=200_000)
        engine = MicaEngine(limits=limits)
        source = (
            'pad = "a"\n'
            "i = 0\n"
            "while i < 12:\n"
            "    pad = pad + pad\n"
            "    i = i + 1\n"
            "d = {}\n"
            "j = 0\n"
            "while j < 100:\n"
            "    d[str(j)] = pad + str(j)\n"
            "    j = j + 1\n"
        )
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_overflow_becomes_catchable_conversion_error(self):
        limits = MicaLimits(max_int_bits=10000)
        source = (
            "x = 2\n"
            "for i in range(13):\n"
            "    x = x * x\n"
            "try:\n"
            "    y = x / 1\n"
            "except conversion_error:\n"
            '    print("overflow caught")\n'
        )
        engine = MicaEngine(limits=limits)
        self.assertEqual(
            engine.create_root(source).run(), "overflow caught\n"
        )

    def test_input_length_limited(self):
        limits = MicaLimits(max_string_bytes=10)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root("x = input()\n", inputs=["x" * 100])
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_source_length_limited(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root("x = 1\n" + "# pad\n" * 800_000)
        self.assertEqual(ctx.exception.kind, "validation_error")

    def test_alloc_charges_ancestors(self):
        child = 's = "a"\nfor i in range(16):\n    s = s + s\n'
        source = (
            "sub = VM(" + repr(child) + ", max_total_bytes=10_000_000)\n"
            "sub.run()\n"
        )
        limits = MicaLimits(max_total_bytes=100_000)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")


class ForLoopTests(unittest.TestCase):
    def test_for_over_list(self):
        source = "t = 0\nfor x in [1, 2, 3]:\n    t = t + x\nprint(t)\n"
        self.assertEqual(run(source), "6\n")

    def test_for_over_dict_keys(self):
        source = 'for k in {"a": 1, "b": 2}:\n    print(k)\n'
        self.assertEqual(run(source), "a\nb\n")

    def test_for_over_string(self):
        source = 'for ch in "ab":\n    print(ch)\n'
        self.assertEqual(run(source), "a\nb\n")

    def test_for_over_range(self):
        source = "for i in range(3):\n    print(i)\n"
        self.assertEqual(run(source), "0\n1\n2\n")

    def test_for_else_without_break(self):
        source = (
            "for i in range(2):\n"
            "    print(i)\n"
            "else:\n"
            '    print("done")\n'
        )
        self.assertEqual(run(source), "0\n1\ndone\n")

    def test_for_else_with_break(self):
        source = (
            "for i in range(5):\n"
            "    if i == 1:\n"
            "        break\n"
            "else:\n"
            '    print("done")\n'
            'print("after")\n'
        )
        self.assertEqual(run(source), "after\n")

    def test_for_continue(self):
        source = (
            "for i in range(4):\n"
            "    if i % 2 == 0:\n"
            "        continue\n"
            "    print(i)\n"
        )
        self.assertEqual(run(source), "1\n3\n")

    def test_nested_for(self):
        source = (
            "for a in [1, 2]:\n"
            "    for b in [10, 20]:\n"
            "        print(a + b)\n"
        )
        self.assertEqual(run(source), "11\n21\n12\n22\n")

    def test_for_not_iterable(self):
        engine = MicaEngine()
        vm = engine.create_root("for x in 5:\n    print(x)\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "type_error")

    def test_for_tuple_target_rejected(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root("for a, b in [1]:\n    pass\n")
        self.assertEqual(ctx.exception.kind, "validation_error")

    def test_while_else(self):
        source = (
            "i = 0\n"
            "while i < 2:\n"
            "    i = i + 1\n"
            "else:\n"
            '    print("done")\n'
        )
        self.assertEqual(run(source), "done\n")

    def test_while_else_skipped_by_break(self):
        source = (
            "while True:\n"
            "    break\n"
            "else:\n"
            '    print("done")\n'
            'print("after")\n'
        )
        self.assertEqual(run(source), "after\n")

    def test_snapshot_hides_for_internals(self):
        engine = MicaEngine()
        vm = engine.create_root("for x in [1]:\n    y = x\n")
        vm.step()
        vm.step()
        for name in vm.snapshot()["locals"]:
            self.assertFalse(name.startswith("$"))


class FinallyTests(unittest.TestCase):
    def test_finally_on_normal_path(self):
        source = "try:\n    x = 1\nfinally:\n    print(\"f\")\n"
        self.assertEqual(run(source), "f\n")

    def test_finally_on_exception(self):
        source = (
            "try:\n"
            "    try:\n"
            "        x = 1 / 0\n"
            "    finally:\n"
            '        print("f")\n'
            "except zero_division:\n"
            '    print("caught")\n'
        )
        self.assertEqual(run(source), "f\ncaught\n")

    def test_finally_with_return(self):
        source = (
            "def f():\n"
            "    try:\n"
            '        return "inner"\n'
            "    finally:\n"
            '        print("cleanup")\n'
            "print(f())\n"
        )
        self.assertEqual(run(source), "cleanup\ninner\n")

    def test_finally_with_break_and_continue(self):
        source = (
            "for i in range(5):\n"
            "    try:\n"
            "        if i == 1:\n"
            "            continue\n"
            "        if i == 3:\n"
            "            break\n"
            "    finally:\n"
            "        print(i)\n"
        )
        self.assertEqual(run(source), "0\n1\n2\n3\n")

    def test_finally_reraises_original_kind(self):
        engine = MicaEngine()
        vm = engine.create_root(
            "try:\n    x = missing\nfinally:\n    y = 1\n"
        )
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "name_error")

    def test_try_except_else(self):
        source = (
            "try:\n"
            "    x = 1\n"
            "except:\n"
            '    print("caught")\n'
            "else:\n"
            '    print("no error")\n'
            "finally:\n"
            '    print("f")\n'
        )
        self.assertEqual(run(source), "no error\nf\n")

    def test_nested_finally_order(self):
        engine = MicaEngine()
        vm = engine.create_root(
            "try:\n"
            "    try:\n"
            '        raise "x"\n'
            "    finally:\n"
            '        print("inner")\n'
            "finally:\n"
            '    print("outer")\n'
        )
        with self.assertRaises(MicaError):
            vm.run()
        self.assertEqual(vm.stdout(), "inner\nouter\n")


class RaiseTests(unittest.TestCase):
    def test_raise_message_catchable(self):
        source = (
            'try:\n'
            '    raise "custom boom"\n'
            "except raise_error as e:\n"
            '    print("caught: " + e)\n'
        )
        self.assertEqual(run(source), "caught: custom boom\n")

    def test_bare_raise_reraises(self):
        source = (
            "try:\n"
            "    try:\n"
            '        raise "inner"\n'
            "    except raise_error:\n"
            "        raise\n"
            "except raise_error as e:\n"
            '    print("got: " + e)\n'
        )
        self.assertEqual(run(source), "got: inner\n")

    def test_raise_non_string_rejected(self):
        engine = MicaEngine()
        vm = engine.create_root("raise 42\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "type_error")

    def test_bare_raise_without_active_error(self):
        engine = MicaEngine()
        vm = engine.create_root("raise\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "raise_error")

    def test_raise_crosses_functions(self):
        source = (
            "def f():\n"
            '    raise "from f"\n'
            "try:\n"
            "    f()\n"
            "except raise_error as e:\n"
            '    print(e)\n'
        )
        self.assertEqual(run(source), "from f\n")


class DefaultParamTests(unittest.TestCase):
    def test_defaults(self):
        source = (
            "def f(a, b=2, c=3):\n"
            "    return a * 100 + b * 10 + c\n"
            "print(f(1))\n"
            "print(f(1, 5))\n"
            "print(f(1, 5, 9))\n"
            "print(f(1, c=7))\n"
        )
        self.assertEqual(run(source), "123\n153\n159\n127\n")

    def test_missing_required_still_arity_error(self):
        engine = MicaEngine()
        vm = engine.create_root("def f(a, b=2):\n    return a\nprint(f())\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "arity_error")

    def test_defaults_evaluated_at_def_time(self):
        source = (
            "x = 10\n"
            "def f(a=x):\n"
            "    return a\n"
            "x = 20\n"
            "print(f())\n"
        )
        self.assertEqual(run(source), "10\n")

    def test_granted_function_with_defaults(self):
        source = (
            "def f(a, b=5):\n"
            "    return a + b\n"
            'sub = VM("print(f(1))\\nprint(f(1, 10))\\n", functions={"f": f})\n'
            "print(sub.run())\n"
        )
        self.assertEqual(run(source), "6\n11\n\n")


class PassTests(unittest.TestCase):
    def test_pass_everywhere(self):
        source = (
            "def f():\n"
            "    pass\n"
            "f()\n"
            "if True:\n"
            "    pass\n"
            "while False:\n"
            "    pass\n"
            'print("ok")\n'
        )
        self.assertEqual(run(source), "ok\n")


class ExternalAuditTests(unittest.TestCase):
    """外部安全审查采纳项的回归测试。"""

    def test_container_membership_charges_work(self):
        source = (
            "big = range(100000)\n"
            "for i in range(20):\n"
            "    if 99999 in big:\n"
            "        pass\n"
        )
        with self.assertRaises(MicaError) as ctx:
            run(source)
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_container_equality_charges_work(self):
        source = (
            "a = range(100000)\n"
            "b = range(100000)\n"
            "for i in range(20):\n"
            "    if a == b:\n"
            "        pass\n"
        )
        with self.assertRaises(MicaError) as ctx:
            run(source)
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_compile_work_charged_to_parent(self):
        big_source = "x = 1\n" * 100000
        source = "sub = VM(" + repr(big_source) + ")\n"
        limits = MicaLimits(max_instructions=100)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_total_vms_limit(self):
        limits = MicaLimits(max_total_vms=3)
        engine = MicaEngine(limits=limits)
        source = "for i in range(10):\n    sub = VM(\"x = 1\\n\")\n"
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_vfs_write_charges_alloc(self):
        limits = MicaLimits(max_total_bytes=1000)
        engine = MicaEngine(limits=limits)
        text = "x" * 200
        vm = engine.create_root(f'write_file("/a.txt", "{text}")\n')
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_string_constant_size_checked(self):
        limits = MicaLimits(max_string_bytes=10)
        engine = MicaEngine(limits=limits)
        vm = engine.create_root('x = "this string is way too long"\n')
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_stdout_method_charges_caller(self):
        limits = MicaLimits(max_total_bytes=1000)
        engine = MicaEngine(limits=limits)
        source = (
            'sub = VM("print(123)")\n'
            "sub.run()\n"
            "i = 0\n"
            "while i < 200:\n"
            "    s = sub.stdout()\n"
            "    i = i + 1\n"
        )
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_for_over_string_charges_alloc(self):
        limits = MicaLimits(max_total_bytes=10_000)
        engine = MicaEngine(limits=limits)
        source = (
            's = "x"\n'
            "for i in range(10):\n"
            "    s = s + s\n"
            "for c in s:\n"
            "    pass\n"
        )
        vm = engine.create_root(source)
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertEqual(ctx.exception.kind, "resource_exhausted")

    def test_nan_to_int_is_conversion_error(self):
        source = (
            "try:\n"
            '    x = int(float("nan"))\n'
            "except conversion_error:\n"
            '    print("caught")\n'
        )
        self.assertEqual(run(source), "caught\n")

    def test_reentrancy_guard(self):
        engine = MicaEngine()
        holder = {}

        def provider():
            holder["vm"].step()
            return "x"

        vm = engine.create_root("x = input()\n", input_provider=provider)
        holder["vm"] = vm
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertIn("already running", str(ctx.exception))

    def test_def_cannot_shadow_builtin(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root("def print():\n    pass\n")
        self.assertEqual(ctx.exception.kind, "validation_error")

    def test_multibyte_source_measured_in_bytes(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root("x = 1\n" + "# 汉\n" * 1_500_000)
        self.assertEqual(ctx.exception.kind, "validation_error")


if __name__ == "__main__":
    unittest.main()


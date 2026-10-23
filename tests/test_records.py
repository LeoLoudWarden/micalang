import unittest

from sericite import SericiteEngine, SericiteError, SericiteLimits


def run(source, inputs=None, limits=None, functions=None):
    engine = SericiteEngine(limits=limits)
    vm = engine.create_root(source, functions=functions)
    return vm.run(inputs)


def run_error(source):
    try:
        run(source)
    except SericiteError as exc:
        return exc
    raise AssertionError("expected SericiteError")


class ConstructionTests(unittest.TestCase):
    def test_positional_construction(self):
        out = run(
            "class Point:\n"
            "    x: int\n"
            "    y: int\n"
            "p = Point(1, 2)\n"
            "print(p.x, p.y)\n"
        )
        self.assertEqual(out, "1 2\n")

    def test_keyword_construction(self):
        out = run(
            "class Point:\n"
            "    x: int\n"
            "    y: int\n"
            "p = Point(y=2, x=1)\n"
            "print(p.x, p.y)\n"
        )
        self.assertEqual(out, "1 2\n")

    def test_bare_field_is_any_contract(self):
        out = run(
            "class Box:\n"
            "    content\n"
            "b = Box('text')\n"
            "print(b.content)\n"
            "b.content = [1, 2]\n"
            "print(b.content)\n"
        )
        self.assertEqual(out, "text\n[1, 2]\n")

    def test_missing_field_rejected(self):
        exc = run_error("class P:\n    x: int\n    y: int\np = P(1)\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("missing", exc.message)
        self.assertIn("y", exc.message)

    def test_unexpected_field_rejected(self):
        exc = run_error("class P:\n    x: int\np = P(1, 2)\n")
        self.assertEqual(exc.kind, "contract_error")
        exc = run_error("class P:\n    x: int\np = P(x=1, z=2)\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("z", exc.message)

    def test_field_contract_violation_at_construction(self):
        exc = run_error("class P:\n    x: int\np = P('nope')\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("x", exc.message)
        self.assertIn("int", exc.message)
        self.assertIn("str", exc.message)

    def test_int_contract_rejects_bool(self):
        exc = run_error("class P:\n    x: int\np = P(True)\n")
        self.assertEqual(exc.kind, "contract_error")

    def test_schema_repr(self):
        out = run("class P:\n    x\n    y: int\nprint(P)\n")
        self.assertEqual(out, "schema(x: any, y: int)\n")


class AttributeAccessTests(unittest.TestCase):
    def test_read_write_field(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "p = P(1)\n"
            "p.x = 42\n"
            "print(p.x)\n"
        )
        self.assertEqual(out, "42\n")

    def test_store_contract_checked(self):
        exc = run_error("class P:\n    x: int\np = P(1)\np.x = 's'\n")
        self.assertEqual(exc.kind, "contract_error")

    def test_missing_field_read(self):
        exc = run_error("class P:\n    x: int\np = P(1)\nprint(p.y)\n")
        self.assertEqual(exc.kind, "attribute_error")
        self.assertIn("'y'", exc.message)

    def test_cannot_add_field(self):
        exc = run_error("class P:\n    x: int\np = P(1)\np.y = 2\n")
        self.assertEqual(exc.kind, "attribute_error")
        self.assertIn("'y'", exc.message)

    def test_dict_has_no_dot_access(self):
        exc = run_error("d = {'a': 1}\nprint(d.a)\n")
        self.assertEqual(exc.kind, "type_error")
        self.assertIn("d[key]", exc.message)

    def test_chained_access(self):
        out = run(
            "class Inner:\n"
            "    v: int\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "o = Outer(Inner(7))\n"
            "print(o.inner.v)\n"
        )
        self.assertEqual(out, "7\n")

    def test_record_not_indexable_not_iterable(self):
        exc = run_error("class P:\n    x: int\np = P(1)\nprint(p[0])\n")
        self.assertEqual(exc.kind, "type_error")
        exc = run_error("class P:\n    x: int\np = P(1)\nfor k in p:\n    pass\n")
        self.assertEqual(exc.kind, "type_error")

    def test_function_fields_allowed(self):
        out = run(
            "class Tool:\n"
            "    name\n"
            "    call\n"
            "def double(n):\n"
            "    return n * 2\n"
            "t = Tool('double', double)\n"
            "print(t.call(21), t.name)\n"
        )
        self.assertEqual(out, "42 double\n")


class EqualityTests(unittest.TestCase):
    def test_structural_equality(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "a = P(1)\n"
            "b = P(1)\n"
            "print(a == b)\n"
        )
        self.assertEqual(out, "True\n")

    def test_same_shape_different_schema_objects_are_equal(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "class Q:\n"
            "    x: int\n"
            "print(P(1) == Q(1))\n"
        )
        self.assertEqual(out, "True\n")

    def test_record_never_equals_dict(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "print(P(1) == {'x': 1})\n"
        )
        self.assertEqual(out, "False\n")

    def test_schema_structural_equality(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "class Q:\n"
            "    x: int\n"
            "class R:\n"
            "    x: str\n"
            "print(P == Q, P == R)\n"
        )
        self.assertEqual(out, "True False\n")

    def test_schema_recreated_each_execution_still_equal(self):
        out = run(
            "def make(n):\n"
            "    class T:\n"
            "        x: int\n"
            "    return T(n)\n"
            "a = make(1)\n"
            "b = make(1)\n"
            "print(a == b)\n"
        )
        self.assertEqual(out, "True\n")


class WrapUnwrapTests(unittest.TestCase):
    def test_roundtrip(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "    y: int\n"
            "p = P(1, 2)\n"
            "d = unwrap(p)\n"
            "print(d)\n"
            "q = wrap(P, d)\n"
            "print(q, p == q)\n"
        )
        self.assertEqual(out, "{'x': 1, 'y': 2}\nrecord(x=1, y=2) True\n")

    def test_unwrap_is_a_copy(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "p = P(1)\n"
            "d = unwrap(p)\n"
            "d['x'] = 99\n"
            "print(p.x)\n"
        )
        self.assertEqual(out, "1\n")

    def test_wrap_strict_key_match(self):
        exc = run_error("class P:\n    x: int\n    y: int\np = wrap(P, {'x': 1})\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("missing", exc.message)
        exc = run_error(
            "class P:\n    x: int\np = wrap(P, {'x': 1, 'z': 2})\n"
        )
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("unexpected", exc.message)

    def test_wrap_checks_contracts(self):
        exc = run_error("class P:\n    x: int\np = wrap(P, {'x': 's'})\n")
        self.assertEqual(exc.kind, "contract_error")

    def test_wrap_unwrap_type_errors(self):
        exc = run_error("p = unwrap({'x': 1})\n")
        self.assertEqual(exc.kind, "type_error")
        exc = run_error("class P:\n    x: int\np = wrap(P, [1])\n")
        self.assertEqual(exc.kind, "type_error")


class AnnotationTests(unittest.TestCase):
    def test_param_contract_accepts_record(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "def get_x(p: P) -> int:\n"
            "    return p.x\n"
            "print(get_x(P(5)))\n"
        )
        self.assertEqual(out, "5\n")

    def test_param_contract_rejects_dict(self):
        # 检疫章规则：形状正确的 dict 也不能替代 record
        exc = run_error(
            "class P:\n"
            "    x: int\n"
            "def f(p: P):\n"
            "    return p.x\n"
            "f({'x': 1})\n"
        )
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("argument p of f", exc.message)
        self.assertIn("got dict", exc.message)

    def test_param_contract_fit_allows_extra_fields(self):
        out = run(
            "class HasX:\n"
            "    x: int\n"
            "class HasXY:\n"
            "    x: int\n"
            "    y: int\n"
            "def f(p: HasX):\n"
            "    return p.x\n"
            "print(f(HasXY(1, 2)))\n"
        )
        self.assertEqual(out, "1\n")

    def test_param_contract_fit_reports_missing(self):
        exc = run_error(
            "class HasXY:\n"
            "    x: int\n"
            "    y: int\n"
            "class HasX:\n"
            "    x: int\n"
            "def f(p: HasXY):\n"
            "    return p.x\n"
            "f(HasX(1))\n"
        )
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("missing: y", exc.message)

    def test_primitive_param_contract(self):
        exc = run_error("def f(n: int):\n    return n\nf('s')\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("argument n of f", exc.message)
        out = run("def f(n: int):\n    return n + 1\nprint(f(1))\n")
        self.assertEqual(out, "2\n")

    def test_return_contract_blame(self):
        exc = run_error("def f() -> int:\n    return 'oops'\nf()\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("return value of f", exc.message)

    def test_annotated_assignment(self):
        out = run("x: int = 3\nprint(x)\n")
        self.assertEqual(out, "3\n")
        exc = run_error("x: int = 's'\n")
        self.assertEqual(exc.kind, "contract_error")
        self.assertIn("annotated assignment", exc.message)

    def test_annotated_assignment_with_schema(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "p: P = P(1)\n"
            "print(p.x)\n"
        )
        self.assertEqual(out, "1\n")
        exc = run_error(
            "class P:\n"
            "    x: int\n"
            "p: P = {'x': 1}\n"
        )
        self.assertEqual(exc.kind, "contract_error")

    def test_annotation_must_be_a_contract(self):
        exc = run_error("def f(p: 123):\n    return p\n")
        self.assertEqual(exc.kind, "contract_error")

    def test_annotation_undefined_name(self):
        exc = run_error("def f(p: Missing):\n    return p\n")
        self.assertEqual(exc.kind, "name_error")

    def test_any_callable_rejected(self):
        exc = run_error("print(any(1))\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_nested_schema_contract(self):
        out = run(
            "class Inner:\n"
            "    v: int\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "def f(o: Outer):\n"
            "    return o.inner.v\n"
            "print(f(Outer(Inner(9))))\n"
        )
        self.assertEqual(out, "9\n")

    def test_nested_schema_contract_violation(self):
        exc = run_error(
            "class Inner:\n"
            "    v: int\n"
            "class Other:\n"
            "    w: int\n"
            "class Outer:\n"
            "    inner: Inner\n"
            "o = Outer(Other(1))\n"
        )
        self.assertEqual(exc.kind, "contract_error")


class LabelTests(unittest.TestCase):
    def test_label_in_attribute_error(self):
        exc = run_error("class Point:\n    x: int\np = Point(1)\nprint(p.y)\n")
        self.assertIn("record Point {x: int}", exc.message)

    def test_label_from_annotated_assignment(self):
        exc = run_error(
            "class T:\n"
            "    x: int\n"
            "def make():\n"
            "    return T(1)\n"
            "r: T = make()\n"
            "print(r.y)\n"
        )
        self.assertIn("record T {x: int}", exc.message)

    def test_unlabeled_value_shows_structure(self):
        exc = run_error(
            "class T:\n"
            "    x: int\n"
            "def make():\n"
            "    return T(1)\n"
            "r = make()\n"
            "print(r.y)\n"
        )
        self.assertIn("record {x: int}", exc.message)
        self.assertNotIn("record T", exc.message)

    def test_param_label_in_body_error(self):
        exc = run_error(
            "class Point:\n"
            "    x: int\n"
            "def f(p: Point):\n"
            "    return p.y\n"
            "f(Point(1))\n"
        )
        self.assertIn("record Point {x: int}", exc.message)


class ClosureAndVMTests(unittest.TestCase):
    def test_schema_captured_by_closure(self):
        out = run(
            "def factory():\n"
            "    class Counter:\n"
            "        n: int\n"
            "    def step(c: Counter):\n"
            "        c.n = c.n + 1\n"
            "        return c.n\n"
            "    return [Counter, step]\n"
            "pair = factory()\n"
            "c = pair[0](0)\n"
            "print(pair[1](c), pair[1](c))\n"
        )
        self.assertEqual(out, "1 2\n")

    def test_granted_function_keeps_contracts_across_vms(self):
        source = (
            "class P:\n"
            "    x: int\n"
            "def get_x(p: P) -> int:\n"
            "    return p.x\n"
            "sub = VM(\"print(f({'x': 1}))\", functions={'f': get_x})\n"
            "sub.run()\n"
        )
        exc = run_error(source)
        self.assertEqual(exc.kind, "contract_error")

    def test_granted_function_labels_do_not_cross_vms(self):
        # 门上的名字是 f；定义方名字 get_x 与类型标签 P 不出现在子层报错里
        source = (
            "class P:\n"
            "    x: int\n"
            "def get_x(p: P) -> int:\n"
            "    return p.x\n"
            "sub = VM(\n"
            "    \"try:\\n\"\n"
            "    \"    f({'x': 1})\\n\"\n"
            "    \"except contract_error as e:\\n\"\n"
            "    \"    print(e)\\n\",\n"
            "    functions={'f': get_x})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("argument 1 of f:", out)
        self.assertIn("expected {x: int}", out)
        self.assertNotIn("get_x", out)
        self.assertNotIn("P {", out)

    def test_granted_function_return_contract_uses_grant_name(self):
        source = (
            "def bad() -> int:\n"
            "    return 'oops'\n"
            "sub = VM(\n"
            "    \"try:\\n\"\n"
            "    \"    g()\\n\"\n"
            "    \"except contract_error as e:\\n\"\n"
            "    \"    print(e)\\n\",\n"
            "    functions={'g': bad})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("return value of g:", out)
        self.assertNotIn("bad", out)

    def test_granted_function_arity_error_uses_grant_name(self):
        source = (
            "def two_args(a, b):\n"
            "    return a\n"
            "sub = VM(\n"
            "    \"try:\\n\"\n"
            "    \"    h(1)\\n\"\n"
            "    \"except arity_error as e:\\n\"\n"
            "    \"    print(e)\\n\",\n"
            "    functions={'h': two_args})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("h() missing argument(s): 2", out)
        self.assertNotIn("two_args", out)

    def test_nested_helper_attr_hint_erased_in_child_vm(self):
        source = (
            "class Secret:\n"
            "    password: str\n"
            "def helper(r: Secret):\n"
            "    return r.nosuch\n"
            "def leak(r):\n"
            "    return helper(r)\n"
            "sub = VM(\"\"\"\n"
            "class C:\n"
            "    password: str\n"
            "try:\n"
            "    leak(C('x'))\n"
            "except attribute_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'leak': leak})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("record {password: str} has no field 'nosuch'", out)
        self.assertNotIn("Secret", out)
        self.assertNotIn("helper", out)

    def test_nested_helper_param_contract_erased_in_child_vm(self):
        source = (
            "class Secret:\n"
            "    password: str\n"
            "def helper(r: Secret):\n"
            "    return 1\n"
            "def leak(r):\n"
            "    return helper(r)\n"
            "sub = VM(\"\"\"\n"
            "class D:\n"
            "    other: int\n"
            "try:\n"
            "    leak(D(1))\n"
            "except contract_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'leak': leak})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("argument 1:", out)
        self.assertIn("expected {password: str}", out)
        self.assertNotIn("Secret", out)
        self.assertNotIn("helper", out)

    def test_nested_helper_return_contract_erased_in_child_vm(self):
        source = (
            "class Secret:\n"
            "    password: str\n"
            "def bad_ret() -> Secret:\n"
            "    return 5\n"
            "def leak():\n"
            "    return bad_ret()\n"
            "sub = VM(\"\"\"\n"
            "try:\n"
            "    leak()\n"
            "except contract_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'leak': leak})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("return value of function:", out)
        self.assertIn("expected {password: str}", out)
        self.assertNotIn("Secret", out)
        self.assertNotIn("bad_ret", out)

    def test_nested_helper_arity_erased_in_child_vm(self):
        source = (
            "def helper(a, b):\n"
            "    return a\n"
            "def leak():\n"
            "    return helper(1)\n"
            "sub = VM(\"\"\"\n"
            "try:\n"
            "    leak()\n"
            "except arity_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'leak': leak})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("function() missing argument(s): 2", out)
        self.assertNotIn("helper", out)

    def test_native_function_repr_safe(self):
        out = run('sub = VM("x = 1")\nf = sub.run\nprint(f)\n')
        self.assertEqual(out, "<function VM.run>\n")

    def test_native_function_type_name_safe(self):
        exc = run_error('sub = VM("x = 1")\nf = sub.run\nlen(f)\n')
        self.assertEqual(exc.kind, "type_error")
        self.assertIn("'function'", exc.message)
        self.assertNotIn("_NativeFunction", exc.message)

    def test_annotation_smuggling_rejected(self):
        exc = run_error(
            "def stash(v):\n"
            "    return 5\n"
            "def f(x: stash(print)):\n"
            "    return x\n"
        )
        self.assertEqual(exc.kind, "validation_error")

    def test_bool_list_dict_contracts(self):
        out = run(
            "x: bool = True\n"
            "y: list = [1]\n"
            "z: dict = {'a': 1}\n"
            "class C:\n"
            "    flag: bool\n"
            "    items: list\n"
            "    table: dict\n"
            "c = C(True, [1], {'a': 1})\n"
            "print(x, y, z, c.flag, c.items, c.table)\n"
        )
        self.assertEqual(out, "True [1] {'a': 1} True [1] {'a': 1}\n")

    def test_bool_list_dict_contract_violations(self):
        exc = run_error("x: bool = 1\n")
        self.assertEqual(exc.kind, "contract_error")
        exc = run_error("y: list = {}\n")
        self.assertEqual(exc.kind, "contract_error")
        exc = run_error("z: dict = [1]\n")
        self.assertEqual(exc.kind, "contract_error")

    def test_contract_only_names_reserved(self):
        for source in ("bool = 5\n", "list = []\n", "dict = {}\n"):
            exc = run_error(source)
            self.assertEqual(exc.kind, "validation_error", msg=source)
        exc = run_error("print(list([1]))\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_field_named_after_builtin(self):
        out = run(
            "class C:\n"
            "    int\n"
            "    print\n"
            "c = C(1, 2)\n"
            "print(c.int, c.print)\n"
        )
        self.assertEqual(out, "1 2\n")

    def test_record_comparison_is_charged(self):
        limits = SericiteLimits(max_instructions=50000)
        source = (
            "class Box:\n"
            "    items\n"
            "a = Box(range(100000))\n"
            "b = Box(range(100000))\n"
            "print(a == b)\n"
        )
        try:
            run(source, limits=limits)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")

    def test_shared_dag_equality_terminates(self):
        out = run(
            "a = [None]\n"
            "b = [None]\n"
            "i = 0\n"
            "while i < 22:\n"
            "    a = [a, a]\n"
            "    b = [b, b]\n"
            "    i = i + 1\n"
            "print(a == b)\n"
        )
        self.assertEqual(out, "True\n")

    def test_contains_no_false_positive_on_shared_substructure(self):
        # 0.4.1 曾把"已比较为 False"的对误判为 True
        out = run(
            "a = [1, 2]\n"
            "b = [a, a]\n"
            "print([99] in b, [1, 2] in b)\n"
        )
        self.assertEqual(out, "False True\n")

    def test_string_membership_is_charged(self):
        limits = SericiteLimits(max_instructions=1_000_000)
        source = (
            's = "x"\n'
            "i = 0\n"
            "while i < 18:\n"
            "    s = s + s\n"
            "    i = i + 1\n"
            "i = 0\n"
            "while i < 100:\n"
            '    found = "needle" in s\n'
            "    i = i + 1\n"
        )
        try:
            run(source, limits=limits)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")

    def test_schema_contract_check_is_charged(self):
        # 每个字段契约是 500 字段的 schema，反复构造必须被计费
        fields = "\n".join(f"    f{i}" for i in range(500))
        limits = SericiteLimits(max_instructions=100000)
        source = (
            f"class Big:\n{fields}\n"
            "class Holder:\n"
            "    payload: Big\n"
            "big = wrap(Big, {"
            + ", ".join(f"'f{i}': 0" for i in range(500))
            + "})\n"
            "i = 0\n"
            "while i < 5000:\n"
            "    h = Holder(big)\n"
            "    i = i + 1\n"
        )
        try:
            run(source, limits=limits)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")

    def test_check_annot_label_erased_in_child_vm(self):
        source = (
            "class Secret:\n"
            "    x: int\n"
            "def helper():\n"
            "    y: Secret = 1\n"
            "    return y\n"
            "def leak():\n"
            "    return helper()\n"
            "sub = VM(\"\"\"\n"
            "try:\n"
            "    leak()\n"
            "except contract_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'leak': leak})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("annotated assignment: expected {x: int}", out)
        self.assertNotIn("Secret", out)

    def test_snapshot_hides_native_function_class(self):
        engine = SericiteEngine()
        vm = engine.create_root(
            'sub = VM("x = 1")\nf = sub.run\nprint(1)\n'
        )
        vm.step()
        vm.step()
        snapshot = vm.step()
        self.assertEqual(snapshot["locals"]["f"], "<function VM.run>")


class HomeVmTests(unittest.TestCase):
    def test_granted_function_builtins_belong_to_home_vm(self):
        source = (
            "def writer():\n"
            "    write_file('/log.txt', 'from home')\n"
            "sub = VM(\"w()\", functions={'w': writer})\n"
            "sub.run()\n"
            "print(path_exists('/log.txt'), sub.path_exists('/log.txt'))\n"
        )
        self.assertEqual(run(source), "True False\n")

    def test_upcalled_function_reads_its_own_vfs(self):
        # 子 VM 交上来的函数被父 VM 调用时，read_file 读的是子 VM 自己的文件系统
        source = (
            "write_file('/secret.txt', 'PARENT SECRET')\n"
            "saved = [None]\n"
            "def stash(fn):\n"
            "    saved[0] = fn\n"
            "sub = VM(\"\"\"\n"
            "def payload():\n"
            "    return read_file('/secret.txt')\n"
            "stash(payload)\n"
            "\"\"\", functions={'stash': stash})\n"
            "sub.run()\n"
            "try:\n"
            "    print(saved[0]())\n"
            "except file_not_found:\n"
            "    print('child vfs has no secret')\n"
        )
        self.assertEqual(run(source), "child vfs has no secret\n")

    def test_granted_function_print_goes_home(self):
        source = (
            "def hello():\n"
            "    print('from home')\n"
            "sub = VM(\"h()\", functions={'h': hello})\n"
            "sub.run()\n"
            "print('child said:', sub.stdout())\n"
        )
        self.assertEqual(run(source), "from home\nchild said: \n")

    def test_granted_function_type_name(self):
        source = (
            "def f():\n"
            "    return 1\n"
            "sub = VM(\"\"\"\n"
            "try:\n"
            "    len(g)\n"
            "except type_error as e:\n"
            "    print(e)\n"
            "\"\"\", functions={'g': f})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertIn("'function'", out)
        self.assertNotIn("_Granted", out)

    def test_spawned_vm_capped_by_caller_limits(self):
        # 子 VM 借授予函数创建的孙 VM，配额同时受调用方收紧：
        # 孙 VM 最多只能跑调用方预算内的指令（resource_exhausted 不可捕获，链式终止）
        child_src = (
            '\nout = spawn("i = 0\\nwhile i < 100000:\\n    i = i + 1")\n'
        )
        source = (
            "def spawn(code):\n"
            "    v = VM(code)\n"
            "    return v.run()\n"
            f"sub = VM({child_src!r}, functions={{'spawn': spawn}}, "
            "max_instructions=1000)\n"
            "sub.run()\n"
        )
        try:
            run(source)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")

    def test_builtin_work_mirrored_to_caller_quota(self):
        # 授予函数的 read_file 作用于 home 的文件系统，
        # 但资源消耗同时计入调用方账本：紧配额的子 VM 读大文件被拦下
        source = (
            'big = "x"\n'
            "i = 0\n"
            "while i < 18:\n"
            "    big = big + big\n"
            "    i = i + 1\n"
            'write_file("/big.txt", big)\n'
            "def peek():\n"
            "    return read_file('/big.txt')\n"
            'sub = VM("""\n'
            "i = 0\n"
            "while i < 5:\n"
            "    s = peek()\n"
            "    i = i + 1\n"
            '""", functions={"peek": peek}, max_total_bytes=2000000)\n'
            "sub.run()\n"
        )
        try:
            run(source)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")
        # 宽配额下同一代码正常完成
        self.assertEqual(
            run(source.replace("max_total_bytes=2000000",
                               "max_total_bytes=50000000")),
            "",
        )

    def test_spawned_vms_bill_caller_cumulatively(self):
        # 经授予函数反复创建孙 VM：孙 VM 的运行期消耗沿调用方祖先链记账，
        # 紧配额的调用方在少量 spawn 后耗尽（而不是每个孙 VM 单独封顶）
        source = (
            "def spawn():\n"
            "    v = VM('i = 0\\nwhile i < 900:\\n    i = i + 1')\n"
            "    return v.run()\n"
            "sub = VM(\n"
            "    'i = 0\\nwhile i < 200:\\n    spawn()\\n    i = i + 1',\n"
            "    functions={'spawn': spawn}, max_instructions=3000)\n"
            "sub.run()\n"
        )
        try:
            run(source)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")

    def test_spawned_vm_attaches_to_caller_tree(self):
        # 授予函数创建的孙 VM 挂在调用方树下（深度与父级按因果归属）
        engine = SericiteEngine()
        source = (
            "def spawn():\n"
            "    return VM('x = 1')\n"
            "sub = VM('g = spawn()\\ng.run()', functions={'spawn': spawn})\n"
            "sub.run()\n"
        )
        engine.create_root(source).run()
        grandchild = engine._contexts[2]
        self.assertEqual(grandchild.depth, 2)
        self.assertEqual(grandchild.parent_id, 1)

    def test_spawn_alloc_not_double_counted(self):
        # 孙 VM 的初始分配沿祖先链走账一次；镜像计费不重复计入调用方
        import json

        grandchild_source = "x = " + json.dumps("y" * 50000)
        engine = SericiteEngine()
        source = (
            "def spawn():\n"
            f"    return VM({grandchild_source!r})\n"
            "sub = VM('g = spawn()', functions={'spawn': spawn})\n"
            "sub.run()\n"
        )
        engine.create_root(source).run()
        child = engine._contexts[1]
        grandchild = engine._contexts[2]
        self.assertEqual(child.alloc_bytes, grandchild.alloc_bytes)

    def test_record_flows_across_vms_by_reference(self):
        source = (
            "class P:\n"
            "    x: int\n"
            "def make():\n"
            "    return P(1)\n"
            "def bump(p):\n"
            "    p.x = p.x + 1\n"
            "sub = VM(\"r = make()\\nbump(r)\\nprint(r.x)\", "
            "functions={'make': make, 'bump': bump})\n"
            "print(sub.run())\n"
        )
        out = run(source)
        self.assertEqual(out, "2\n\n")

    def test_record_rejected_at_host_boundary(self):
        def identity(value):
            return value

        source = (
            "class P:\n"
            "    x: int\n"
            "print(host(P(1)))\n"
        )
        try:
            run(source, functions={"host": identity})
        except SericiteError as exc:
            self.assertEqual(exc.kind, "ffi_error")
        else:
            raise AssertionError("expected ffi_error")


class QuotaTests(unittest.TestCase):
    def test_record_allocation_counted(self):
        limits = SericiteLimits(max_total_bytes=2000)
        source = (
            "class P:\n"
            "    x: int\n"
            "i = 0\n"
            "while i < 1000:\n"
            "    p = P(i)\n"
            "    i = i + 1\n"
        )
        try:
            run(source, limits=limits)
        except SericiteError as exc:
            self.assertEqual(exc.kind, "resource_exhausted")
        else:
            raise AssertionError("expected resource_exhausted")


class ValidationTests(unittest.TestCase):
    def test_class_defaults_rejected(self):
        exc = run_error("class P:\n    x = 1\n")
        self.assertEqual(exc.kind, "validation_error")
        self.assertIn("defaults", exc.message)
        exc = run_error("class P:\n    x: int = 1\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_class_methods_rejected(self):
        exc = run_error("class P:\n    def m():\n        pass\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_class_bases_rejected(self):
        exc = run_error("class P(Base):\n    x\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_duplicate_field_rejected(self):
        exc = run_error("class P:\n    x\n    x: int\n")
        self.assertEqual(exc.kind, "validation_error")
        self.assertIn("duplicate", exc.message)

    def test_bare_annotation_rejected(self):
        exc = run_error("x: int\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_dunder_field_rejected(self):
        exc = run_error("class P:\n    __x\n")
        self.assertEqual(exc.kind, "validation_error")

    def test_builtin_redefine_rejected(self):
        exc = run_error("class len:\n    x\n")
        self.assertEqual(exc.kind, "validation_error")
        exc = run_error("wrap = 1\n")
        self.assertEqual(exc.kind, "validation_error")
        exc = run_error("any = 1\n")
        self.assertEqual(exc.kind, "validation_error")


class ReprTests(unittest.TestCase):
    def test_record_str(self):
        out = run(
            "class P:\n"
            "    x: int\n"
            "    name\n"
            "print(P(1, 'a'))\n"
        )
        self.assertEqual(out, "record(x=1, name='a')\n")

    def test_record_cycle_safe(self):
        out = run(
            "class Node:\n"
            "    next\n"
            "n = Node(None)\n"
            "n.next = n\n"
            "print(n)\n"
        )
        self.assertEqual(out, "record(next=record(...))\n")


class SnapshotTests(unittest.TestCase):
    def test_record_in_snapshot(self):
        engine = SericiteEngine()
        vm = engine.create_root("class P:\n    x: int\np = P(1)\nprint(p.x)\n")
        snap = vm.step()
        snap = vm.step()
        self.assertIn("<record>", str(snap["locals"]))


if __name__ == "__main__":
    unittest.main()

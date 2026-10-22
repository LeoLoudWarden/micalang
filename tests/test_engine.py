import unittest

from mica import MicaEngine, MicaError

ADD_PROGRAM = """\
a = input()
b = input()
c = a + b
print(c)
"""

SUB_VM_PROGRAM = """\
vm_code = \"\"\"
a = input()
b = input()
c = a + b
print(c)
\"\"\"
sub = VM(vm_code)
sub_output = sub.run(["2", "3"])
print(sub_output)
"""


class RootVMTests(unittest.TestCase):
    def test_run(self):
        engine = MicaEngine()
        vm = engine.create_root(ADD_PROGRAM)
        output = vm.run(["2", "3"])
        self.assertEqual(output, "23\n")
        self.assertEqual(vm.snapshot()["state"], "done")

    def test_step_by_step(self):
        engine = MicaEngine()
        vm = engine.create_root(ADD_PROGRAM, inputs=["2", "3"])

        snap = vm.step()
        self.assertEqual(snap["state"], "ready")
        self.assertEqual(snap["locals"], {"a": "2"})
        self.assertEqual(snap["last_line"], 1)
        self.assertEqual(snap["next_line"], 2)

        snap = vm.step()
        self.assertEqual(snap["locals"], {"a": "2", "b": "3"})

        snap = vm.step()
        self.assertEqual(snap["locals"]["c"], "23")

        snap = vm.step()
        self.assertEqual(snap["state"], "done")
        self.assertEqual(snap["stdout"], "23\n")

        snap = vm.step()
        self.assertEqual(snap["state"], "done")

    def test_waiting_input_and_resume(self):
        engine = MicaEngine()
        vm = engine.create_root(ADD_PROGRAM)

        snap = vm.step()
        self.assertEqual(snap["state"], "waiting_input")
        self.assertEqual(snap["locals"], {})

        vm.feed_input("2")
        snap = vm.step()
        self.assertEqual(snap["state"], "ready")
        self.assertEqual(snap["locals"], {"a": "2"})

        vm.feed_input("3")
        output = vm.run()
        self.assertEqual(output, "23\n")

    def test_empty_program_is_done(self):
        engine = MicaEngine()
        vm = engine.create_root("")
        self.assertEqual(vm.snapshot()["state"], "done")
        self.assertEqual(vm.run(), "")


class SubVMTests(unittest.TestCase):
    def test_sub_vm_io_isolated(self):
        engine = MicaEngine()
        vm = engine.create_root(SUB_VM_PROGRAM)
        output = vm.run()
        self.assertEqual(output, "23\n\n")

    def test_handle_repr_shows_id_and_state(self):
        engine = MicaEngine()
        vm = engine.create_root(SUB_VM_PROGRAM)
        vm.step()
        vm.step()
        sub = vm.snapshot()["locals"]["sub"]
        self.assertIn("id=1", sub)
        self.assertIn("state=ready", sub)

    def test_nested_vm_creation(self):
        mid_src = 'inner = VM("print(\'deep\')")\nprint(inner.run())\n'
        engine = MicaEngine()
        vm = engine.create_root(f"sub = VM({mid_src!r})\nprint(sub.run())\n")
        self.assertEqual(vm.run(), "deep\n\n\n")

    def test_debug_sub_vm_from_root(self):
        engine = MicaEngine()
        vm = engine.create_root(
            'sub = VM("a = input()\\nprint(a)")\n'
            "first = sub.step()\n"
            'sub.feed_input("hi")\n'
            "sub.step()\n"
            "last = sub.step()\n"
            "print(sub.stdout())\n"
        )
        output = vm.run()
        self.assertEqual(output, "hi\n\n")

    def test_layer1_can_create_vm(self):
        mid_src = 'inner = VM("x = 1")\nprint(inner.snapshot())\n'
        engine = MicaEngine()
        vm = engine.create_root(f"sub = VM({mid_src!r})\nprint(sub.run())\n")
        output = vm.run()
        self.assertIn("'state': 'ready'", output)

    def test_child_vm_stdout_does_not_leak_to_parent(self):
        engine = MicaEngine()
        vm = engine.create_root(
            'sub = VM("print(\\"inner\\")")\n'
            "sub.run()\n"
            'print("outer")\n'
        )
        output = vm.run()
        self.assertEqual(output, "outer\n")


class ValidationTests(unittest.TestCase):
    def test_syntax_error(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError) as ctx:
            engine.create_root("a = = 1\n")
        self.assertEqual(ctx.exception.kind, "syntax_error")

    def test_unsupported_syntax_rejected(self):
        engine = MicaEngine()
        for source in ("import os\n", "del x\n", "class A:\n    pass\n"):
            with self.assertRaises(MicaError, msg=source):
                engine.create_root(source)

    def test_dunder_names_rejected(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError):
            engine.create_root("x = __builtins__\n")
        with self.assertRaises(MicaError):
            engine.create_root('sub = VM("x = 1")\nsub.__class__()\n')

    def test_reserved_names_call_only(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError):
            engine.create_root("f = input\n")
        with self.assertRaises(MicaError):
            engine.create_root("x = VM\n")

    def test_unsupported_attribute_rejected(self):
        engine = MicaEngine()
        with self.assertRaises(MicaError):
            engine.create_root('sub = VM("x = 1")\nsub.evil()\n')

    def test_runtime_error_reported(self):
        engine = MicaEngine()
        vm = engine.create_root("x = missing\n")
        with self.assertRaises(MicaError) as ctx:
            vm.run()
        self.assertIn("missing", ctx.exception.message)
        self.assertEqual(vm.snapshot()["state"], "error")
        self.assertIsNotNone(vm.snapshot()["error"])

    def test_bad_add_operands(self):
        engine = MicaEngine()
        vm = engine.create_root('x = "a" + 1\n')
        with self.assertRaises(MicaError):
            vm.run()


if __name__ == "__main__":
    unittest.main()

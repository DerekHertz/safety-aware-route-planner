import sys

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

extra_compile_args = []
if sys.platform == "win32":
    # /fp:precise + "each add its own statement" code shape in engine.cpp
    # prevents FMA/contraction — required for bitwise parity with pyref.
    # Escalate to /fp:strict only if the parity suite ever shows 1-ulp drift.
    extra_compile_args = ["/O2", "/fp:precise", "/permissive-", "/EHsc",
                          "/DNOMINMAX"]
else:
    extra_compile_args = ["-O2", "-ffp-contract=off"]

ext_modules = [
    Pybind11Extension(
        "sr_core",
        ["src/engine.cpp", "src/bindings.cpp"],
        cxx_std=17,
        extra_compile_args=extra_compile_args,
    ),
]

setup(
    name="sr-core",
    version="0.1.0",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)

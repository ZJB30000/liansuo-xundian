"""测试包：与 src/my_tool 结构对应。

说明：本机 site-packages 里存在第三方库（ultralytics）自带的同名 tests 包，
加 __init__.py 让 pytest 以包模式解析，保证这里的 tests 优先于全局包。
"""

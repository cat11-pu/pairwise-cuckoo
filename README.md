# cuckoo

一小套布谷鸟哈希表内核：两张等长的槽位表、两套内置哈希、踢出重排、踢出上限与
扩容重哈希、双表查找与删除清槽。键限 int 与 str，可用自带的稳定哈希，也可以
由调用方注入一对哈希函数。只使用 Python 标准库，不调用内置 hash()，结果与会
话的 PYTHONHASHSEED 无关，不做任何 I/O。

## 目录

- cuckoo/core.py 双表存槽、踢出重排、扩容重哈希、查找与删除
- tests/test_core.py 行为与结构测试

## 运行测试

在项目根目录（本文件所在目录）执行：

    python3 -m unittest discover -s tests -v

全部用例通过时进程退出码为 0。若系统里没有 `python3`，把命令里的 `python3`
换成 `python` 即可（Windows 上常见）。

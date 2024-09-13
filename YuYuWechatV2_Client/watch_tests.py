import os
import time
import hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 用于存储文件的哈希值，防止重复执行
file_hashes = {}

class TestRunnerEventHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory:
            return  # 忽略目录修改
        print(f"{event.src_path} modified, checking file...")
        # 检查文件内容是否真正发生变化
        if self.has_file_changed(event.src_path):
            print(f"Changes detected in {event.src_path}, running tests...")
            os.system('python manage.py test --verbosity 2')
        else:
            print(f"No actual content changes in {event.src_path}, skipping tests.")

    def has_file_changed(self, file_path):
        """检查文件内容是否发生变化，通过计算文件的哈希值来判断"""
        try:
            with open(file_path, 'rb') as file:
                file_content = file.read()
                file_hash = hashlib.md5(file_content).hexdigest()

            # 如果文件路径之前没有记录，或者哈希值发生了变化，则表示文件内容改变了
            if file_path not in file_hashes or file_hashes[file_path] != file_hash:
                file_hashes[file_path] = file_hash
                return True
        except FileNotFoundError:
            # 文件可能在事件发生时被删除，忽略错误
            return False

        return False


if __name__ == "__main__":
    path = '.'  # 监控当前目录及子目录
    event_handler = TestRunnerEventHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)  # 递归监控子目录
    observer.start()

    try:
        while True:
            time.sleep(1)  # 防止CPU占用过高
    except KeyboardInterrupt:
        observer.stop()

    observer.join()
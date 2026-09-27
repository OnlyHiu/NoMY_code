import logging
import os
import subprocess

module_logger = logging.getLogger("flu_widget.Extended_Call_Script")


class ExtendedCallScript:
    def __init__(self, exepath):
        self.exepath = exepath

        if exepath[-4:] == ".bat":
            module_logger.info("执行bat文件")
            self.extended_call_bat()
        else:
            module_logger.info("执行exe文件")
            self.extended_call_exe()

    def extended_call_exe(self):
        order = [self.exepath]
        cwd_path = os.path.dirname(self.exepath)
        module_logger.info(f"执行命令:{str(order)},执行路径:{cwd_path}")
        try:
            # subprocess.Popen(order,
            #                  stdout=subprocess.PIPE,
            #                  stderr=subprocess.STDOUT,
            #                  text=True,
            #                  encoding='utf-8',
            #                  shell=True,
            #                  cwd=cwd_path)
            subprocess.Popen(order, shell=True, cwd=cwd_path)
            return True
        except Exception as e:
            return e

    def extended_call_bat(self):
        order = [self.exepath]
        cwd_path = os.path.dirname(self.exepath)
        module_logger.info("执行命令:" + str(order))
        try:
            subprocess.Popen(order, encoding='utf-8', cwd=cwd_path)
            return True
        except Exception as e:
            return e

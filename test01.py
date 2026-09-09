import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import torch
import torch.nn as nn

def dm_rnn_for_base():
  # 1.创建GRU模型对象
  gru = nn.GRU(5,6,1)
  # 2.创建输入数据
  input = torch.randn(2,3,5)
  # 3.创建初始隐藏状态
  h0 = torch.randn(1,3,6)
  
  # 4.运行GRU模型
  output, h = gru(input, h0)
  
  # 5.打印输出结果
  print(output)


if __name__ == '__main__':
  dm_rnn_for_base()



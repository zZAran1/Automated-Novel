"""全局共享配置常量。"""

# 每章正文的目标字数范围（prompt 引导模型用），与参考上限一起使用
CHAPTER_TARGET_CHARS = (1500, 2000)
# 每章正文的参考上限（正文生成校验用），非硬性要求
CHAPTER_MAX_CHARS = 2000
# 字数超出参考上限的容忍量：超出不足 1000 字不作废，超出达到 1000 字直接抛异常作废
CHAPTER_OVERFLOW_TOLERANCE = 1000
# 章节情节概要的输入长度上限，与正文字数解耦，两者改动互不影响
SUMMARY_MAX_CHARS = 2500

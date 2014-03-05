baidu-fuse
==========

###### 需要的库
fuse (http://fuse.sourceforge.net)

baidupcsapi: https://github.com/ly0/baidupcsapi

fusepy: https://github.com/terencehonles/fusepy

建议将 fuse 的 FUSE_MIN_READ_BUFFER 改成 131072 (内核似乎最大支持128k)
可以提高下载速度

```Shell
mkdir ./mnt 
python baidufuse.py USERNAME PASSWORD mnt
```

*强烈建议在CLI下访问挂载点，用GUI下的文件浏览器会请求每个文件的首部，在文件数目多的时候会造成速度过慢*

基于百度网盘的一个fuse，实验性项目

实现列表 
* 文件列表
* 基本的文件上传下载
* 重命名

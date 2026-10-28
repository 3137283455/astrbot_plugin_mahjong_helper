"""Render the public help poster and matching user guide from one content source."""
from __future__ import annotations
import argparse,json,math
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
W=2000;M=58;GAP=32;CW=(W-M*2-GAP)//2
PAPER='#F5EFE3';INK='#202126';ORANGE='#EC5936';MUTED='#66635F';LINE='#D8CFC0';WHITE='#FFFCF6';GOLD='#F4C65B'
EDITION='2026.10.28'
MODULES=[
 {'id':'01','title':'群数据','tag':'只看本群精华','subtitle':'被飞几次，谁最惨？看这里。','height':770,'lines':[
  ('cmd','/群数据'),('small','三麻、四麻独立总览；不填模式分别显示。'),
  ('cmd','/群数据 被飞榜 四麻'),('cmd','/群数据 个人 I咲仡'),
  ('small','个人卡片包含被飞次数、被飞率与顺位。'),
  ('small','还可查：被飞率、一位 / 二位及比率、最高 / 最低。'),
  ('cmd','/群数据 最高 四麻　 /群数据 原图 编号'),
  ('note','只收本群精华中的完整结算图；文字、牌桌图跳过。'),
  ('note','被飞＝最终持点＜0；比例仅代表本群精华样本。'),
  ('note','约2分钟检查新增精华；识别完成才计入，重复去重。'),
  ('note','首次需群管理员 /群数据 开启；只查询当前群。'),
  ('link','更多：/群数据 帮助　进度：/群数据 状态')],
  'example':'@慧慧 看看本群四麻谁被飞最多'},
 {'id':'02','title':'雀魂战绩','tag':'牌谱屋公开数据','subtitle':'查数据、看卡片，也可以让慧慧锐评。','height':770,'lines':[
  ('alert','仅收录金之间及以上的公开对局'),
  ('cmd','/雀 玩家昵称 三 铳 30天'),
  ('cmd','/雀 玩家昵称 四 顺 玉 90天'),
  ('small','昵称放前面，三 / 四、栏目、房间、时间放后面。'),
  ('small','后缀参数可换序；默认四麻，无数据再查三麻。'),
  ('small','栏目：基 / 顺 / 立 / 风 / 和 / 运 / 铳 / 近 / 桌 / 局 / 页'),
  ('cmd','/雀 搜 昵称　→　/雀 绑 UID'),
  ('small','/雀 号 看绑定；/雀 切 UID 换主绑定；/雀 解 UID'),
  ('small','绑定后可省略昵称，例如：/雀 基。'),
  ('note','可绑多个公开UID；多人可绑同一UID，不验证归属。'),
  ('note','筛选：金 / 玉 / 王；7天 / 30天 / 90天 / 365天。'),
  ('note','近 / 桌 / 局如遇访问验证，会返回玩家网页链接。')],
  'example':'@慧慧 锐评一下我的雀魂战绩'},
 {'id':'03','title':'何切练习','tag':'601道原书题','subtitle':'先想好切哪张，再看答案。','height':530,'lines':[
  ('cmd','/何切　　/何切 150'),
  ('small','随机出题 / 指定题号；每轮随机不重复。'),
  ('cmd','/何切答案　　/何切状态'),
  ('small','看当前题答案与解析 / 看本轮进度。'),
  ('note','不自动判题；更多用法：/何切 帮'),
  ('cmd','/符数　 符数规则 + 番符点数对照表')],
  'example':'@慧慧 来5道何切题'},
 {'id':'04','title':'点歌与歌单','tag':'歌单优先','subtitle':'先找已收藏的歌，没有再在线搜索。','height':530,'lines':[
  ('cmd','/搜索歌曲 晴天　　/搜索视频 BV号'),
  ('small','出现列表后，5分钟内可续接：播放第二首。'),
  ('small','也能说：把第二首加入歌单。群聊聊天请@慧慧。'),
  ('cmd','/歌单　 /歌单 加 2　 /歌单 播 1'),
  ('small','查看 / 收藏搜索结果 / 播放；/歌单 删 1 删除。'),
  ('note','个人歌单长期保存；/歌单 加 可收藏刚播放的歌。')],
  'example':None},
 {'id':'05','title':'B站订阅','tag':'动态与直播提醒','subtitle':'订阅当前聊天，更新推送也发到这里。','height':540,'lines':[
  ('cmd','/B站 订阅 UID　　/B站 取消 UID'),
  ('cmd','/B站 列表　　/B站 状态'),
  ('cmd','/B站 测试 UID'),
  ('small','重发最新可见动态，可反复查看。'),
  ('small','过滤示例：/B站 订阅 UID lottery forward'),
  ('note','上例排除抽奖与转发；UID是B站个人主页的数字。'),
  ('note','首次需管理员私聊 /bili_login 扫码登录。'),
  ('link','完整命令、过滤项和提醒设置：/B站')],
  'example':None},
 {'id':'06','title':'友人房广播','tag':'QQ群功能','subtitle':'房间开好了？一条消息喊人来。','height':540,'lines':[
  ('cmd','/友 房间号或链接'),
  ('small','房间内容会原样广播，并@全体成员。'),
  ('small','可填房间号、链接或其他简短字符串。'),
  ('alert','需机器人为群管理员，且有@全体权限和次数。'),
  ('note','同一用户：第2次需距上次5分钟；'),
  ('note','第3次起，每次需距上次15分钟。'),
  ('note','发送成功后才开始计冷却。'),
  ('link','这是房间广播；不读取友人房战绩。')],
  'example':None},
]


@lru_cache(maxsize=32)
def font(size,bold=False):
 for p,index in [('/usr/share/fonts/opentype/noto/NotoSansCJK-'+('Bold' if bold else 'Regular')+'.ttc',2),('C:/Windows/Fonts/'+('msyhbd' if bold else 'msyh')+'.ttc',0)]:
  if Path(p).is_file():return ImageFont.truetype(p,size,index=index)
 raise RuntimeError('需要中文字体 Noto Sans CJK 或微软雅黑')


def snapshot_catalog(pool_path):
 data=json.loads(Path(pool_path).read_text(encoding='utf-8-sig'))
 if not isinstance(data,list):raise ValueError('娱乐项目池必须是列表')
 return {key:[e['name'] for e in data if e.get('type')==key and e.get('enabled',True) and not e.get('scope')] for key in ('text','image','video','audio')}


def load_catalog(path=None):
 if path is None:
  path=ROOT/'data'/'help_catalog.json'
 data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
 if not all(isinstance(data.get(k),list) and all(isinstance(s,str) for s in data[k]) for k in ('text','image','video','audio')):raise ValueError('娱乐项目目录不完整')
 return data


class Poster:
 def __init__(self,height):
  self.im=Image.new('RGB',(W,height),PAPER);self.d=ImageDraw.Draw(self.im);self.bounds=[]
 def text(self,x,y,text,size=30,bold=False,color=INK,width=None):
  f=font(size,bold);b=self.d.textbbox((x,y),text,font=f);limit=W-x-M if width is None else width
  if b[2]-x>limit+1:raise ValueError(f'文字超过栏宽：{text} ({b[2]-x}>{limit})')
  if b[0]<0 or b[1]<0 or b[2]>W or b[3]>self.im.height:raise ValueError('文字超过画布：'+text)
  self.d.text((x,y),text,font=f,fill=color);self.bounds.append((text,b));return b
 def pill(self,x,y,text,color=ORANGE,fill=None,size=25):
  width=math.ceil(self.d.textlength(text,font=font(size,True)))+30
  self.d.rounded_rectangle((x,y,x+width,y+43),radius=10,fill=fill or INK)
  self.text(x+15,y+3,text,size,True,color,width=width-25);return width
 def panel(self,x,y,w,h):
  self.d.rectangle((x+7,y+8,x+w+7,y+h+8),fill=INK)
  self.d.rectangle((x,y,x+w,y+h),fill=WHITE,outline=INK,width=2)
 def module(self,x,y,item):
  h=item['height'];self.panel(x,y,CW,h)
  self.d.rectangle((x,y,x+100,y+95),fill=ORANGE)
  self.text(x+17,y+12,item['id'],50,True,WHITE,width=80)
  self.text(x+126,y+19,item['title'],43,True,width=CW-150)
  self.text(x+126,y+77,item['subtitle'],27,color=MUTED,width=CW-154)
  self.text(x+27,y+123,item['tag'],24,True,ORANGE,width=CW-52)
  yy=y+168
  styles={'cmd':(32,True,INK,46),'small':(28,False,INK,42),'note':(26,False,MUTED,40),'link':(27,True,INK,43),'alert':(28,True,ORANGE,43)}
  for kind,line in item['lines']:
   size,bold,color,line_h=styles[kind];self.text(x+28,yy,line,size,bold,color,width=CW-56);yy+=line_h
  if item['example']:
   if yy+43>y+h-16:raise ValueError('示例超过模块高度：'+item['title'])
   self.d.rectangle((x+20,yy+4,x+CW-20,yy+47),fill='#F8E6D7')
   self.text(x+29,yy+9,item['example'],26,True,width=CW-58);yy+=51
  if yy>y+h-10:raise ValueError('模块文字溢出：'+item['title'])
 def catalog_grid(self,x,y,w,names,columns=3,size=29,line_h=48):
  col=w/columns
  for i,name in enumerate(names):
   xx=x+(i%columns)*col;yy=y+(i//columns)*line_h
   self.d.rectangle((xx,yy+15,xx+5,yy+29),fill=ORANGE)
   self.text(xx+17,yy,name,size,width=col-28)
  return y+math.ceil(len(names)/columns)*line_h


def render(path,catalog_path=None):
 c=load_catalog(catalog_path);total=sum(len(c[k]) for k in ('text','image','video','audio'));p=Poster(4300);d=p.d
 # Editorial cover with a drawn magic burst: type stays crisp at original size.
 p.pill(M,38,'HUIHUI / 功能导航',WHITE,INK,26)
 p.text(M,100,'慧慧',108,True,width=650);p.text(M+280,146,'今天想做点什么？',49,True,width=1000)
 p.text(M+4,240,'练何切 · 查符数 · 查雀魂 · 看群数据 · 听歌 · 订阅B站 · 娱乐',31,color=MUTED,width=1600)
 d.rectangle((1694,35,1942,255),fill=ORANGE)
 p.text(1721,64,'HELP',60,True,WHITE,width=200);p.text(1723,156,'一图使用手册',25,True,WHITE,width=200)
 # Star / rays are decoration rather than an image-generated character.
 for k in range(8):
  a=k*math.pi/4;cx,cy=1580,126;r=44
  d.line((cx+math.cos(a)*r*.4,cy+math.sin(a)*r*.4,cx+math.cos(a)*r,cy+math.sin(a)*r),fill=ORANGE,width=5)
 d.rectangle((M,304,W-M,452),fill=INK)
 p.text(M+28,324,'群聊：@慧慧 + 你的需求',36,True,WHITE,width=900)
 p.text(1060,326,'私聊：直接说；常规命令可直接发',30,True,WHITE,width=850)
 p.text(M+28,389,'例如：来道何切题 / 播放第二首 / 看看四麻被飞榜 / 给我功能菜单',28,color='#E6D9C8',width=1800)
 commands=['/群数据','/雀','/何切','/符数','/歌单','/B站','/娱乐']
 qw=(W-2*M-(len(commands)-1)*16)/len(commands)
 for i,s in enumerate(commands):
  x=M+i*(qw+16);d.rectangle((x,479,x+qw,544),fill=WHITE,outline=INK,width=2);p.text(x+24,488,s,34,True,width=qw-40)
 y=578
 for i in range(0,len(MODULES),2):
  p.module(M,y,MODULES[i]);p.module(M+CW+GAP,y,MODULES[i+1]);y+=max(MODULES[i]['height'],MODULES[i+1]['height'])+32
 # Entertainment names come from a sanitized snapshot of the actual enabled catalog.
 p.text(M,y+3,'07',54,True,ORANGE,width=100);p.text(M+118,y+12,'娱乐百宝箱',43,True,width=500)
 p.text(M+635,y+26,f'{total} 项速查 · 不知道怎么说，就让慧慧帮你找',28,color=MUTED,width=1200)
 y+=91
 d.rectangle((M,y,W-M,y+111),fill='#F8D9B2',outline=INK,width=2)
 p.text(M+26,y+14,'群聊务必同一条消息@慧慧，娱乐菜单也一样；私聊直接发送。',31,True,width=1800)
 p.text(M+26,y+64,'@慧慧 /娱乐　　@慧慧 /娱乐 图片　　@慧慧 搜图 猫　　@慧慧 今日老婆',28,width=1800)
 y+=138;cat_h=1020
 p.panel(M,y,CW,cat_h);p.panel(M+CW+GAP,y,CW,cat_h)
 p.text(M+26,y+23,f'文字 / 查询　{len(c["text"])}项',35,True,width=CW-50)
 end=p.catalog_grid(M+27,y+88,CW-50,c['text'],size=29,line_h=49)
 p.text(M+29,end+30,'项目名后可加参数；不知道填什么，',27,color=MUTED,width=CW-58)
 p.text(M+29,end+70,'私聊或@慧慧发送：/查看api 项目名',27,color=MUTED,width=CW-58)
 d.rectangle((M+25,y+cat_h-135,M+CW-25,y+cat_h-25),fill=GOLD)
 p.text(M+42,y+cat_h-118,'不想背关键词？说「想看今天的老婆」',28,True,width=CW-90)
 p.text(M+42,y+cat_h-72,'或「给我来点摆烂文案」也可以。',28,True,width=CW-90)
 x=M+CW+GAP;yy=y+23
 for key,label in [('image','图片'),('video','视频'),('audio','语音')]:
  p.text(x+26,yy,f'{label}　{len(c[key])}项',35,True,width=CW-50);yy+=62
  yy=p.catalog_grid(x+27,yy,CW-50,c[key],size=28,line_h=46)+22
 if yy>y+cat_h-12:raise ValueError('娱乐列表超过模块')
 y+=cat_h+32
 d.rectangle((M,y,W-M,y+183),fill=INK)
 p.text(M+27,y+18,'还有这些小功能',34,True,WHITE,width=700)
 p.text(M+27,y+77,'表情包：/meme status 看状态；/meme list 看已收集的表情。',29,color=WHITE,width=1800)
 p.text(M+27,y+125,'原神语录：启用的群里，普通消息发送「原神」即可触发。',29,color='#E6D9C8',width=1800)
 y+=220
 p.text(M,y,'继续看详细菜单：/群数据 帮助 · /雀 帮 · /何切 帮 · /B站',29,True,width=1850)
 p.text(M,y+51,'功能管理、登录、数据修正与@全体提醒设置，请联系管理员。',28,color=MUTED,width=1850)
 p.text(M,y+100,'娱乐内容来自第三方，部分项目需参数或可能临时不可用；以 /娱乐 当前列表为准。',26,color=MUTED,width=1850)
 d.line((M,y+162,W-M,y+162),fill=INK,width=2)
 p.text(M,y+185,'忘记怎么用，就发 /help 或 /帮助。',36,True,width=1300)
 p.text(1535,y+199,'更新 '+EDITION,25,color=MUTED,width=420)
 bottom=y+257
 if bottom>p.im.height:raise ValueError(f'画布高度不够：{bottom}')
 p.im=p.im.crop((0,0,W,bottom));path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);p.im.save(path,'PNG',optimize=True)
 return path


def write_manual(path,catalog_path=None):
 c=load_catalog(catalog_path);lines=['# 慧慧功能使用手册','',f'更新：{EDITION}。群聊自然语言请求请@慧慧，私聊直接说；普通命令可直接发。娱乐命令和菜单在群聊也要@慧慧。','']
 for item in MODULES:
  lines += ['## '+item['title'],'',item['subtitle'],'']+[('- `'+line+'`') if kind=='cmd' else '- '+line for kind,line in item['lines']]
  if item['example']:lines += ['',item['example']]
  lines += ['']
 lines+=['## 娱乐百宝箱','','群聊使用：`@慧慧 /娱乐`、`@慧慧 /娱乐 图片`；私聊可省略@。项目名后可带参数，如 `@慧慧 搜图 猫`。']
 for key,label in [('text','文字'),('image','图片'),('video','视频'),('audio','语音')]:lines+=['','### '+label,'','、'.join(c[key])+'。']
 lines+=['','项目来自第三方，部分项目需参数或可能临时不可用；以 `/娱乐` 当前列表为准。','', '## 其他功能','','表情包：`/meme status` 查看状态，`/meme list` 查看已收集表情。启用的群里，普通消息发送“原神”可触发原神语录。','','帮助：`/help` 或 `/帮助`。']
 Path(path).write_text('\n'.join(lines)+'\n',encoding='utf8')

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'data/help_one_page.png');parser.add_argument('--catalog',type=Path);parser.add_argument('--pool',type=Path,help='从实际项目池提取公共项目名称，不保存接口地址');args=parser.parse_args()
 if args.pool:
  (ROOT/'data/help_catalog.json').write_text(json.dumps(snapshot_catalog(args.pool),ensure_ascii=False,indent=2),encoding='utf8')
 print(render(args.output,args.catalog));write_manual(ROOT/'docs/慧慧使用手册.md',args.catalog)

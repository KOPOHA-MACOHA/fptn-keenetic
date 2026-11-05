#!/bin/sh
# Developer-only passive profiler. Never included in installed runtime packages.
set -eu
export LC_ALL=C LANG=C
ROOT=${FPTN_PREFIX:-/opt}
RUN=$ROOT/var/run/fptn-keenetic
fail() { echo "$*" >&2; exit 1; }
resource_report() {
  DURATION=${1:-60}; INTERVAL=${2:-2}
  case "$DURATION:$INTERVAL" in *[!0-9:]*|:*|*:) fail 'Использование: sh tests/profile-resources.sh [секунды 2–3600] [интервал 1–60].' ;; esac
  [ "${#DURATION}" -le 4 ] && [ "${#INTERVAL}" -le 2 ] &&
    [ "$DURATION" -ge 2 ] && [ "$DURATION" -le 3600 ] &&
    [ "$INTERVAL" -ge 1 ] && [ "$INTERVAL" -le 60 ] && [ "$INTERVAL" -le "$DURATION" ] || fail 'Неверная длительность или интервал.'
  # A single persistent awk, plus sleep; no ps/top, daemon, network or temp files.
  # BEGIN PASSIVE RESOURCE READER
  awk -v run="$RUN" -v duration="$DURATION" -v interval="$INTERVAL" '
    function first(path, line) {line=""; getline line < path; close(path); return line}
    function cpu( line,a,n,i) {
      total=idle=0; line=first("/proc/stat"); n=split(line,a," ")
      # guest/guest_nice are already included in user/nice.
      for(i=2;i<=9 && i<=n;i++)total+=a[i]
      idle=a[5]+a[6]
    }
    function memory( line,a) {
      available=-1
      while((getline line < "/proc/meminfo")>0){split(line,a," ");if(a[1]=="MemAvailable:")available=a[2]+0}
      close("/proc/meminfo")
    }
    function process(role, record,a,line,b,path,ticks,key,delta,pct,rss) {
      record=first(run "/" role ".pid"); split(record,a," ")
      if(a[1]!~/^[0-9]+$/ || a[2]!~/^[0-9]+$/ || a[3]!=boot){previous[role]=""; return}
      path="/proc/" a[1]; line=first(path "/stat")
      if(line==""){previous[role]=""; return}
      sub(/^.*\) /,"",line); split(line,b," ")
      if(b[20]!=a[2] || b[1]=="Z" || b[1]=="X"){previous[role]=""; return}
      ticks=b[12]+b[13]; key=a[1] ":" a[2]; pct=-1
      if(previous[role]==key && dt>0 && ticks>=lastticks[role])pct=100*(ticks-lastticks[role])/dt
      else if(!seen[role,key]++)instances[role]++
      previous[role]=key; lastticks[role]=ticks
      rss=0
      while((getline line < (path "/status"))>0){split(line,b," ");if(b[1]=="VmRSS:")rss=b[2]+0}
      close(path "/status")
      # Do not publish a sample if the process disappeared or its PID was reused.
      line=first(path "/stat");sub(/^.*\) /,"",line);split(line,b," ")
      if(line=="" || b[20]!=a[2]){previous[role]="";return}
      if(rss>rssmax[role])rssmax[role]=rss
      if(pct>=0){sumticks[role]+=pct*dt/100; sumdt[role]+=dt; if(pct>peak[role])peak[role]=pct; count[role]++}
      printf "[РЕСУРСЫ] %s: CPU %s; RSS %d КиБ.\n",role,(pct<0?"н/д":sprintf("%.1f%%",pct)),rss
    }
    BEGIN {
      boot=first("/proc/sys/kernel/random/boot_id")
      print "Пассивный замер: 100% CPU = все логические процессоры вместе. RSS не равен приватной памяти."
      print "Без тестового трафика, изменения DNS, маршрутов или перезапуска службы."
      cpu(); oldtotal=total; oldidle=idle; dt=0
      process("client"); process("supervisor")
      started=first("/proc/uptime")+0; previousTime=started; elapsed=0
      if(boot=="" || total<=0 || started<=0)exit 1
      while(elapsed<duration){
        wait=interval;if(duration-elapsed<wait)wait=int(duration-elapsed)+1
        if(system("sleep " wait)!=0)exit 1
        now=first("/proc/uptime")+0
        if(now<=previousTime)exit 1
        previousTime=now; elapsed=now-started
        cpu(); dt=total-oldtotal; di=idle-oldidle; oldtotal=total; oldidle=idle
        if(dt<=0 || di<0 || di>dt){
          # Linux iowait may move backwards. Rebaseline process counters too,
          # otherwise their next delta would span two system CPU intervals.
          dt=0;process("client");process("supervisor");continue
        }
        busy=100*(dt-di)/dt; allbusy+=dt-di; allticks+=dt;if(busy>allpeak)allpeak=busy
        memory();if(available>=0 && (!haveavail || available<minavail)){minavail=available;haveavail=1}
        printf "[РЕСУРСЫ] %.1f с: общий CPU %.1f%%; MemAvailable %d КиБ.\n",elapsed,busy,available
        process("client");process("supervisor");fflush()
      }
      if(allticks>0)printf "[ИТОГ] общий CPU: средний %.1f%% / пик %.1f%%; минимум MemAvailable %d КиБ.\n",100*allbusy/allticks,allpeak,(haveavail?minavail:-1)
      for(i=1;i<=2;i++){
        role=(i==1?"client":"supervisor")
        printf "[ИТОГ] %s: CPU %s / пик %s; максимум RSS %d КиБ; процессов %d.\n",role,(sumdt[role]>0?sprintf("%.1f%%",100*sumticks[role]/sumdt[role]):"н/д"),(count[role]?sprintf("%.1f%%",peak[role]):"н/д"),rssmax[role],instances[role]
      }
      print "Нагрузка других процессов включена только в общий CPU. При смене PID первый интервал CPU пропущен."
    }'
  # END PASSIVE RESOURCE READER
}

resource_report "${1:-60}" "${2:-2}"

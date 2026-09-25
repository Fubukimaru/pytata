#!/bin/bash

WORK=25
PAUSE=5
POMODORI=4
NUM_BEEP=5
TASK=$(task next limit:1 | tail -n +4 | head -n 1 | sed 's/^ //' | cut -d ' ' -f1)
INTERACTIVE=true
MUTE=false
STATUSFILE=false

show_help() {
	cat <<-END
		usage: potato [-s] [-m] [-w m] [-b m] [-p i] [-t t] [-h] [-o f] [-f t]
		    -s: simple output. Intended for use in scripts
		        When enabled, potato outputs one line for each minute, and doesn't print the bell character
		        (ascii 007)

		    -m: mute -- don't play sounds when work/break is over
		    -w m: let work periods last m minutes (default is 20)
		    -b m: let break periods last m minutes (default is 5)
		    -p i: let iterate of pomodori before the big break (default is 4)
		    -t t: let task ID from Taskwarrior to start (default is the most urgent task)
		    -o f: print all the information to the file f
		    -f t: filter the most prioritary task with tags t 
		    -h: print this message
	END
}

controlc() {
    # Secure shutdown
    printf "\rSIGINT caught      "
    if [ -n $STATUSFILE ]; then 
        rm $STATUSFILE
    fi
    task $TASK stop

    exit
}

trap 'controlc' SIGINT

play_notification() {
    for run in {1..$NUM_BEEP}; do
        read -t 0.1 -n 1 unused && break
        aplay -q ~/share/linux/patata/notification.wav
    done
}




while getopts :sw:b:p:t:mo:f: opt; do
	case "$opt" in
	s)
		INTERACTIVE=false
	;;
	m)
		MUTE=true
	;;
	w)
		WORK=$OPTARG
	;;
	b)
		PAUSE=$OPTARG
	;;
	p)
		POMODORI=$OPTARG
	;;
	t)
		TASK=$OPTARG
	;;
    f)
        TASK=$(task $OPTARG next limit:1 | tail -n +4 | head -n 1 | sed 's/^ //' | cut -d ' ' -f1)
    ;;
    o)
        STATUSFILE=$OPTARG
    ;;

	h|\?)
		show_help
		exit 1
	;;
	esac
done

time_left="%im left of %s"
time_left_file="%im left of %s\n"

if $INTERACTIVE; then
    task $TASK list
	time_left="\r$time_left"
else
    task $TASK list
	time_left="$time_left\n"
fi

# while true
for ((p=$POMODORI; p>0; p--))
do
    echo $p
    task $TASK start

	for ((i=$WORK; i>0; i--))
	do
		printf "$time_left" $i "work"
        if [ -n $STATUSFILE ]; then
		    printf "$time_left_file" $i "work" > $STATUSFILE
        fi
		sleep 1m
	done

	! $MUTE && play_notification
	if $INTERACTIVE; then
		read -d '' -t 0.001
		echo -e "\a"
		echo "Work over"
        if [ -n $STATUSFILE ]; then
    		echo -e "Work over. ¡Estira!\n" > $STATUSFILE
        fi
		read
	fi

    # Pauses are included in the count
    # task $TASK stop

    if [ $p -ne 1 ]; then
        for ((i=$PAUSE; i>0; i--))
        do
            printf "$time_left" $i "pause"
            if [ -n $STATUSFILE ]; then 
                printf "$time_left_file" $i "pause" > $STATUSFILE
            fi
            sleep 1m
        done

        ! $MUTE && play_notification
        if $INTERACTIVE; then
            read -d '' -t 0.001
            echo -e "\a"
            echo "Pause over"

            if [ -n $STATUSFILE ]; then 
                echo -e "Pause over\n" > $STATUSFILE
            fi
            read
        fi
    fi
done

task $TASK stop
echo "Take a coffee break! ☕"


if [ -n $STATUSFILE ]; then 
    rm $STATUSFILE
fi

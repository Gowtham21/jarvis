import pyttsx3
import speech_recognition as sr
import datetime
import wikipedia
import webbrowser
import os
import smtplib
import requests
import wolframalpha
import random




engine = pyttsx3.init('sapi5')
client = wolframalpha.Client("##################") #login to wolframalpha and get Client ID and replace this #
rate = engine.getProperty('rate')   # getting details of current speaking rate
print (rate)                        #printing current voice rate
engine.setProperty('rate', 150)     # setting up new voice rate
volume = engine.getProperty('volume')   #getting to know current volume level (min=0 and max=1)
print (volume)                          #printing current volume level
engine.setProperty('volume',1.0)    # setting up volume level  between 0 and 1
voices = engine.getProperty('voices')
for voice in voices:
    print(voice)
    engine.setProperty('voice', voices[0].id)

#speak function will speak what we give
def speak(text):
    engine.say(text)
    engine.runAndWait()

MASTER = input("enter your name to call you =")

#this function will wish you at current time
def wishMe():
    hour = int(datetime.datetime.now().hour)
    print(hour)
    if hour >= 0 and hour < 12:
        speak("Good morning" + MASTER)
    elif hour >= 12 and hour < 18:
        speak("Good afternoon" + MASTER)
    elif hour >= 18 and hour < 20:
        speak("Good evening" + MASTER)
    else:
        speak("Good night" + MASTER)

#this function will take command from the microphone
def takeCommand():
    r = sr.Recognizer()
    with sr.Microphone() as source:
        #speak("Listening...")
        audio = r.listen(source)
    try:
        #speak("Recognizing...")
        query = r.recognize_google(audio, language = 'en-in').lower()
        print(f"{MASTER}: {query}\n")

    except Exception as e:
        query = takeCommand()
    return query

#main program is here
def main():
    while True:
        query = takeCommand()
        if 'jarvis' in query:
            speak("hey")
            query = takeCommand()

            #logic for executing task as per the query
            if 'wikipedia' in query:
                speak('Searching in wikipedia...')
                query = query.replace("wikipedia","")
                results = wikipedia.summary(query, sentences = 2)
                print(results)
                speak(results)
            elif 'name' in query:
                speak(MASTER)

            elif 'open youtube' in query:
                url = "youtube.com"
                chrome_path = 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe %s'
                webbrowser.get(chrome_path).open(url)

            elif 'in google' in query:
                chrome_path = 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe %s'
                try:
                    from googlesearch import search
                except ImportError:
                    print("No module named 'google' found")
                # to search
                query = query
                for j in search(query, tld="co.in", num=10, stop=1, pause=2):
                    print(j)
                    webbrowser.get(chrome_path).open(j)

            elif 'greet me' in query:
                wishMe()

            elif 'play music' in query:
                songs_dir = "################" # copy your music folder path and replace here instead of #
                songs = os.listdir(songs_dir)
                print(songs)
                os.startfile(os.path.join(songs_dir, songs[1]))

            elif 'play video' in query:
                video_dir = "##################" # copy your video folder path and replace here instead of #
                video = os.listdir(video_dir)
                print(video)
                os.startfile(os.path.join(video_dir, video[0]))

            elif 'the time' in query:
                strTime = datetime.datetime.now().strftime("%H:%M:%S")
                speak(f"{MASTER} the time is {strTime}")

            elif 'game' in query:
                speak(f"alright {MASTER}, will play head or tail")
                def flip():
                    f = random.randint(0,1)
                    if(f==0):
                        return "head"
                    else:
                        return "tail"
                #n = input("Head or tail  ")
                while True:
                    speak("Toss")
                    query = takeCommand()
                    if 'head' in query:
                        w = flip()
                        if 'head' == w:
                            speak("you won")
                            print(w)
                        elif w == 'tail':
                            speak("you lost")
                            print(w)
                        else:
                            speak("tell only head or tail")
                    elif 'tail' in query:
                        w = flip()
                        if 'tail' == w:
                            speak("you won")
                        elif 'head' == w:
                            speak("you lost")
                        else:
                            speak("tell only head or tail")
                    elif 'stop' in query:
                        speak("enjoyed the game bye")
                        break
            elif 'no' in query:
                speak(f" okay {MASTER},")

            elif 'stop' in query:
                speak("okay I will wait for you next time bye")

            elif 'do something' in query:
                speak("what I will do am not human to enjoy")

            elif 'do more' in query:
                speak("you get lost from here")

            else:
                query = query
                speak(f'{MASTER} give some moment...')
                try:
                    try:
                        res = client.query(query)
                        results = next(res.results).text
                        speak('I got')
                        speak(results)
                    except:
                        results = wikipedia.summary(query, sentences=3)
                        speak("Got it")
                        speak("Wikipedia says...")
                        speak(results)
                except:
                    speak("can not able to get, Please check manually in chrome")
                    chrome="C:/Program Files (x86)/Google/Chrome/Application/chrome.exe %s"
                    webbrowser.get(chrome).open("https://www.google.com")

        elif 'bye' in query:
            speak("thank you")
            break

main()

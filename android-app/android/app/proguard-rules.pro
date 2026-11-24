# Release builds are not minified (minifyEnabled false). If minification is turned on later, keep the plugin
# and Capacitor's reflective entry points:
-keep class com.goppo.chat.GoppoNativePlugin { *; }
-keep class com.getcapacitor.** { *; }
-keepattributes *Annotation*

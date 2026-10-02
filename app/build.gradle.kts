plugins {
    id("com.android.application")
}

android {
    namespace = "com.sahn.youtubedownloader"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.sahn.youtubedownloader"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
}

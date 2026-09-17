import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

val releaseKeystoreProperties = Properties()
val releaseKeystorePropertiesFile = rootProject.file("key.properties")
val hasReleaseSigning = releaseKeystorePropertiesFile.isFile
if (hasReleaseSigning) {
    FileInputStream(releaseKeystorePropertiesFile).use(releaseKeystoreProperties::load)
}

android {
    namespace = "com.oohstory.oohstory"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = "27.0.12077973"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_11.toString()
    }

    defaultConfig {
        applicationId = "com.oohstory.oohstory"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                val configuredStoreFile = releaseKeystoreProperties.getProperty("storeFile")
                    ?: error("key.properties is missing storeFile")
                storeFile = rootProject.file(configuredStoreFile)
                storePassword = releaseKeystoreProperties.getProperty("storePassword")
                    ?: error("key.properties is missing storePassword")
                keyAlias = releaseKeystoreProperties.getProperty("keyAlias")
                    ?: error("key.properties is missing keyAlias")
                keyPassword = releaseKeystoreProperties.getProperty("keyPassword")
                    ?: error("key.properties is missing keyPassword")
            }
        }
    }

    buildTypes {
        release {
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    applicationVariants.all {
        val variant = this
        variant.outputs.all {
            val output = this as com.android.build.gradle.internal.api.BaseVariantOutputImpl
            val abi = output.getFilter(com.android.build.OutputFile.ABI)
            val abiSuffix = abi?.let { "-$it" }.orEmpty()
            output.outputFileName =
                "OOHStory-v${variant.versionName}+${variant.versionCode}$abiSuffix.apk"
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    // Bundled on-device models: OCR never requires an image upload.
    implementation("com.google.mlkit:text-recognition:16.0.1")
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
}

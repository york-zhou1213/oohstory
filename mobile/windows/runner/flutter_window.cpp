#include "flutter_window.h"

#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <winrt/Windows.Globalization.h>
#include <winrt/Windows.Graphics.Imaging.h>
#include <winrt/Windows.Media.Ocr.h>
#include <winrt/Windows.Storage.Streams.h>
#include <winrt/base.h>

#include "flutter/generated_plugin_registrant.h"

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  ocr_channel_ = std::make_unique<
      flutter::MethodChannel<flutter::EncodableValue>>(
      flutter_controller_->engine()->messenger(),
      "com.oohstory.oohstory/local_ocr",
      &flutter::StandardMethodCodec::GetInstance());
  ocr_channel_->SetMethodCallHandler(
      [](const flutter::MethodCall<flutter::EncodableValue>& call,
         std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
        if (call.method_name() != "recognize") {
          result->NotImplemented();
          return;
        }
        const auto* arguments = std::get_if<flutter::EncodableMap>(call.arguments());
        if (arguments == nullptr) {
          result->Error("OCR_INVALID_IMAGE", "OCR arguments are missing");
          return;
        }
        const auto bytes_it = arguments->find(flutter::EncodableValue("bytes"));
        const auto locale_it = arguments->find(flutter::EncodableValue("locale"));
        if (bytes_it == arguments->end()) {
          result->Error("OCR_INVALID_IMAGE", "OCR image is empty");
          return;
        }
        const auto* bytes = std::get_if<std::vector<uint8_t>>(&bytes_it->second);
        if (bytes == nullptr || bytes->empty()) {
          result->Error("OCR_INVALID_IMAGE", "OCR image is empty");
          return;
        }
        std::string locale = "zh-Hans";
        if (locale_it != arguments->end()) {
          if (const auto* requested = std::get_if<std::string>(&locale_it->second)) {
            locale = *requested;
          }
        }
        std::thread(
            [image_bytes = *bytes, locale = std::move(locale),
             result = std::move(result)]() mutable {
              try {
                winrt::init_apartment(winrt::apartment_type::multi_threaded);
                using namespace winrt::Windows::Graphics::Imaging;
                using namespace winrt::Windows::Storage::Streams;
                InMemoryRandomAccessStream stream;
                DataWriter writer(stream);
                writer.WriteBytes(image_bytes);
                writer.StoreAsync().get();
                writer.FlushAsync().get();
                writer.DetachStream();
                stream.Seek(0);
                const auto decoder = BitmapDecoder::CreateAsync(stream).get();
                const auto bitmap = decoder.GetSoftwareBitmapAsync(
                    BitmapPixelFormat::Bgra8,
                    BitmapAlphaMode::Premultiplied).get();

                const wchar_t* language_tag =
                    locale.rfind("zh", 0) == 0 ? L"zh-Hans" : L"en-US";
                const winrt::Windows::Globalization::Language language(language_tag);
                if (!winrt::Windows::Media::Ocr::OcrEngine::IsLanguageSupported(language)) {
                  result->Error(
                      "OCR_UNAVAILABLE",
                      locale.rfind("zh", 0) == 0
                          ? "Windows Chinese OCR language pack is not installed"
                          : "Windows English OCR language pack is not installed");
                  return;
                }
                const auto engine =
                    winrt::Windows::Media::Ocr::OcrEngine::TryCreateFromLanguage(language);
                if (engine == nullptr) {
                  result->Error("OCR_UNAVAILABLE", "Windows OCR engine is unavailable");
                  return;
                }
                const auto recognized = engine.RecognizeAsync(bitmap).get();
                const std::string text = winrt::to_string(recognized.Text());
                flutter::EncodableMap response;
                response[flutter::EncodableValue("text")] =
                    flutter::EncodableValue(text);
                response[flutter::EncodableValue("confidence")] =
                    flutter::EncodableValue(text.empty() ? 1.0 : 0.9);
                result->Success(flutter::EncodableValue(response));
              } catch (const winrt::hresult_error& error) {
                result->Error("OCR_FAILED", winrt::to_string(error.message()));
              }
            })
            .detach();
      });
  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  if (flutter_controller_) {
    ocr_channel_.reset();
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}

# HAP编译构建指导

## 概述
此指导用于在OpenHarmony中通过gn实现完整应用的编译、签名和镜像安装。
### 基本概念
| 术语 | 含义 |
| -------------- | ---------------------- |
| HAP            | OpenHarmony Ability Package，一个HAP文件包含应用的所有内容，由代码、资源、三方库及应用配置文件组成，其文件后缀名为.hap。 |
| APP            | OpenHarmony Application Package, 一个APP文件包含多个Hap，文件后缀名为.app。 |
| Ability        | 应用的重要组成部分，是应用所具备能力的抽象。Ability是系统调度应用的最小单元，是能够完成一个独立功能的组件，一个应用可以包含一个或多个Ability。 |
| FA             | Feature Ability，是FA模型的Ability框架下具有UI界面的Ability类型，用于与用户进行交互。Feature Ability唯一对应一种模板，即Page模板（Page Ability）。 |
| PA             | Particle Ability，是在FA模型的Ability框架下无界面的Ability，主要为Feature Ability提供服务与支持，例如作为后台服务提供计算能力，或作为数据仓库提供数据访问能力。Particle Ability有三种模板，分别为Service模板（Service Ability）、Data模板（Data Ability）、以及Form模板（Form Ability）。 |
| FA模型         | 两种Ability框架模型结构的其中一种。是Ability框架在API 8及更早版本采用FA模型。FA模型将Ability分为FA（Feature Ability）和PA（Particle Ability）两种类型，其中FA支持Page Ability模板，PA支持Service ability、Data ability、以及Form ability模板。详情可参考[FA模型综述](https://gitee.com/openharmony/docs/blob/master/zh-cn/application-dev/application-models/fa-model-development-overview.md)。 |
| Stage模型 | 两种Ability框架模型结构的其中一种。从API 9开始支持。Stage模型将Ability分为Ability和ExtensionAbility两大类，其中ExtensionAbility又被扩展为ServiceExtensionAbility、FormExtensionAbility、DataShareExtensionAbility等等一系列ExtensionAbility。详情可参考[Stage模型综述](https://gitee.com/openharmony/docs/blob/master/zh-cn/application-dev/application-models/stage-model-development-overview.md)。 |

### 功能简介
提供HAP和APP编译功能

## 开发指导

### 编译系统提供的模板
#### ohos_app

声明一个APP目标，该目标会生成一个或多个HAP，最终将会打包到system镜像中。

| 支持的变量 | 说明 |
| --------- | ---- |
| **hap_name** | 指定输出hap的名称，仅用于应用工程中只包含单个hap的场景，默认值为"${target_name}" |
| **build_profile** | 默认为build-profile.json5 |
| **system_lib_deps** | 依赖的系统库 |
| **module_libs_dir** | 所依赖的系统库在应用中的目录，与**system_lib_deps**共同使用，默认为"entry" |
| **deps** | 当前目标的依赖 |
| **subsystem_name** | (required) APP从属的子系统名，需要和ohos.build中的名字对应，否则将导致无法安装到system镜像中。|
| **part_name** | (required) APP从属的部件名，同subsystem_name。|
| **module_install_dir** | 安装到system中的位置，默认安装在system/app目录下。|
| **js_build_mode** | 可选，用于配置HAP是“release”还是“debug”模型编译，默认“release”。|
| **install_enable** | 是否安装到镜像，默认为true |
| **hap_out_dir** | 单个应用中所有hap的输出目录，默认为"${target_out_dir}/${target_name}" |
| **test_hap** | 对应用中的HAP测试ohosTest进行编译, 默认为false |
| **test_module** | 编译的测试用例ohosTest所在的模块名，与**test_hap**共同使用，默认为entry |
| **sdk_home** | 用于指定sdk的所在路径, 默认为"//prebuilts/ohos-sdk/linux" |
| **sdk_type_name** | 用于指定sdk类型, 默认为["sdk.dir"] |
| **build_level** | 用于指定应用的编译级别，默认为"project", 可以指定为"module" |
| **assemble_type** | 用于指定应用产物类型，默认为"AssembleApp", 可以指定为"AssembleHap" |



### 操作步骤

1. 将开发完成的应用example放到应用归档目录下，例如目录"applications/standard"。

2. 配置gn脚本applications/standard/example/BUILD.gn，简单示例如下：
   ```
   import("//build/ohos.gni") # 引用ohos.gni

   ohos_app("example") {
     part_name = "prebuilt_hap"   # 必选
     subsystem_name = "applications"  # 必选
     certificate_profile = "./signature/example.p7b"  # 必选
     system_lib_deps = [ "//applications/standard/MyApplication8/telephony_data:tel_telephony_data_test" ] # 依赖系统库，需要时添加
     module_libs_dir = "entry"  # 使用system_lib_deps时指定
   }
   ```

3. 修改applications/standard/hap/ohos.build，示例如下：
   ```
   {
     "subsystem": "applications",
     "parts": {
       "prebuilt_hap": {
         "module_list": [
           ...
           "//applications/standard/example:example" # 添加编译目标
         ]
       }
     }
   }
   ```

4. 编译命令：
   ```
   # 全量编译
   ./build.sh --product-name {product_name}

   # 单独编译APP
   ./build.sh --product-name {product_name} --build-target applications/standard/example:example
   ```

5. 编译产物，简单例子HAP解压视图如下：
   ```
   .
   ├── ets
   │   ├── entryability
   │   │   └── EntryAbility.abc
   │   └── pages
   │       └── Index.abc
   ├── libs
   │   └── armeabi-v7a
   │       ├── libc++_shared.so
   │       ├── libentry.so
   │       └── libtel_telephony_data_test.z.so
   ├── module.json
   ├── pack.info
   ├── resources
   │   └── base
   │       ├── media
   │       │   ├── app_icon.png
   │       │   └── icon.png
   │       └── profile
   │           └── main_pages.json
   └── resources.index
   ```

### 注意事项：
1. xts应用须使用ohos_js_app_suite模板，该模板是对ohos_app的封装，支持参数与ohos_app相同，差异点在于**hap_out_dir**默认在"out/{product_name}/suites/{xts_type}/testcases/"下。模板使用示例如下：
   ```
   import("//test/xts/tool/build/suite.gni")

   ohos_js_app_suite("test_example") {
     hap_name = "test_example"
     testonly = true
     certificate_profile = "./signatrue/test_example.p7b"
     subsystem_name = "common"
     part_name = subsystem_name
   }
   ```

2. 如果使用应用提供的ohosTest测试Hap编译功能，需要单独创建测试hap的目标并使能test_hap字段，同时指定对应的test_module, 例如：
   ```
   import("//build/ohos.gni")
   
   ohos_hap("example") {
    hap_name = "example"
    certificate_profile = "./signatrue/example.p7b"
    subsystem_name = "common"
    part_name = subsystem_name
   }

   ohos_hap("test_example") {
    hap_name = "test_example"
    certificate_profile = "./signatrue/example.p7b"
    test_hap = true
    test_module = "entry"
    subsystem_name = "common"
    part_name = subsystem_name
   }
   ```
   上面的模板将会编译出example.hap和test_example.hap，其中test_example.hap是由entry/ohosTest中的源码编译产生。

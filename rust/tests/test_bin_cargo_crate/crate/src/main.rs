/*
 * Copyright (c) 2023 Huawei Device Co., Ltd.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
//! test_bin_cargo_crate
fn main() {
    println!("Hello, world!");
    #[cfg(is_new_rustc)]
    println!("Is new rustc!");
    #[cfg(is_old_rustc)]
    println!("Is old rustc!");
    #[cfg(is_ohos)]
    println!("Is ohos!");
    #[cfg(is_mac)]
    println!("Is darwin!");
}
